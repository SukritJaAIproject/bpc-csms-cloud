#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Flexxfast Virtual Charger — OCPP 1.6-J charge point simulator
=============================================================
A stand-in for a real charging cabinet so the CSMS Web UI can be demonstrated
and taught without hardware.  It behaves like a 2-connector DC charger:

  * connects to the CSMS (ws://host:9000/ocpp/<CP_ID>, subprotocol ocpp1.6),
    sends BootNotification / Heartbeat / StatusNotification
  * answers every CSMS command the Flexxfast CSMS can send:
    RemoteStart/StopTransaction, Reset, UnlockConnector, ChangeAvailability,
    Get/ChangeConfiguration (incl. EnableFrontButtons{N}, CentralSystemURL,
    ChargeBoxId), ClearCache, DataTransfer (PEA Ping/GetVersion/...),
    TriggerMessage, ReserveNow/CancelReservation, Set/ClearChargingProfile,
    GetCompositeSchedule, GetLocalListVersion/SendLocalList, GetDiagnostics
    (uploads a zip to the CSMS), UpdateFirmware
  * simulates the driver through a small web "front panel":
    plug in / unplug the cable, tap an RFID card, press the Start/Stop buttons,
    pay by QR (DataTransfer PaymentReceived/PaymentSettled), trip a fault,
    cut the network, reboot
  * produces MeterValues (Transaction.Begin / Sample.Periodic / Sample.Clock /
    Transaction.End / Trigger) with SoC, power, current, voltage, temperature

Dependencies (same as the CSMS): websockets>=11, aiohttp>=3.8
Run:  python mock_cp.py                      (finds a Flexxfast CSMS on this PC / in the LAN by itself)
      python mock_cp.py --url 192.168.1.10   (a specific CSMS; ws://...:9000/ocpp is added)
      python mock_cp.py --url ws://127.0.0.1:9000/ocpp --id SIM-001
Panel: http://127.0.0.1:8082/   (first free port from --panel-port upwards)
The CSMS is found through csms_runtime.json (written by a CSMS started from the same folder), a UDP
broadcast on port 9077 that every running Flexxfast CSMS answers, or a scan of ports 9000-9009 locally.
Settings changed on the panel page are kept in charger_config.json next to the program.

This is a simulator for training and interoperability testing only.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import logging
import math
import os
import random
import re
import sys
import time
import uuid
import webbrowser
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

try:
    import websockets
    from websockets.exceptions import ConnectionClosed
except ImportError:  # pragma: no cover
    print("Please install: pip install websockets")
    sys.exit(1)
try:
    import aiohttp
    from aiohttp import web
except ImportError:  # pragma: no cover
    print("Please install: pip install aiohttp")
    sys.exit(1)

LOG = logging.getLogger("mock_cp")
FIRMWARE_VERSION = "SIM-1.0.3"
PAY_CURRENCY = "BTN"               # Ngultrum (Bhutan) for the simulated charger-side payment
PAY_TARIFF_PER_KWH = 9.0           # Nu. per kWh used by the simulated charger terminal
FROZEN = bool(getattr(sys, "frozen", False))            # packaged with PyInstaller
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(os.path.abspath(__file__)).parent
CALL, CALLRESULT, CALLERROR = 2, 3, 4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: Optional[datetime] = None) -> str:
    return (dt or utc_now()).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(value: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def as_bool(value: Any) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Connection settings helpers (what people type is rarely a full ws:// URL)
# ---------------------------------------------------------------------------
DEFAULT_OCPP_PORT = 9000
DEFAULT_OCPP_PATH = "/ocpp"
CONFIG_FILE_NAME = "charger_config.json"


def normalize_csms_url(raw: Any, cp_id: str = "") -> str:
    """Turn whatever was typed into a CSMS base URL (without the charge point id).

    Accepts  192.168.1.10  ->  ws://192.168.1.10:9000/ocpp
             csms-pc:8000  ->  ws://csms-pc:8000/ocpp
             http://10.0.0.5:9000/ocpp  ->  ws://10.0.0.5:9000/ocpp
             ws://10.0.0.5:9000/ocpp/SIM-001 (with cp_id SIM-001) -> ws://10.0.0.5:9000/ocpp
    Returns "" when nothing usable can be made of it.
    """
    from urllib.parse import urlsplit

    text = str(raw or "").strip().strip("'\"")
    if not text:
        return ""
    low = text.lower()
    if low.startswith("http://"):
        text = "ws://" + text[7:]
    elif low.startswith("https://"):
        text = "wss://" + text[8:]
    elif not low.startswith(("ws://", "wss://")):
        if "://" in text:                 # some other scheme: not usable
            return ""
        text = "ws://" + text
    try:
        parts = urlsplit(text)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return ""
    if not host or any(ch.isspace() for ch in host) or parts.netloc.endswith(":"):
        return ""
    if ":" in host and not host.startswith("["):   # bare IPv6 literal
        host = f"[{host}]"
    path = parts.path.rstrip("/")
    if cp_id and path.endswith("/" + cp_id):      # user pasted the full URL incl. the CP id
        path = path[: -len(cp_id) - 1]
    if not path:
        path = DEFAULT_OCPP_PATH
    if port is None and parts.scheme == "wss":    # [VC-CLOUD] TLS without a port: the standard 443 (cloud CSMS)
        return f"wss://{host}{path}"
    if port is None:
        port = DEFAULT_OCPP_PORT
    return f"{parts.scheme}://{host}:{port}{path}"


def clean_cp_id(raw: Any) -> str:
    """Charge point identity as used in the URL path: no slashes / blanks, max 48 chars."""
    text = "".join(ch for ch in str(raw or "").strip() if not ch.isspace() and ch not in "/\\?#")
    return text[:48]


def describe_connect_error(exc: BaseException, url: str) -> str:
    """One readable line for the panel instead of a Python exception name."""
    name = type(exc).__name__
    text = str(exc)
    low = text.lower()
    if name == "InvalidURI" or "isn't a valid uri" in low:
        return f"'{url}' is not a valid URL - use ws://<csms-ip>:9000/ocpp (fix it in the Connection box)"
    if "refused" in low or name == "ConnectionRefusedError" or "10061" in text or "1225" in text:
        return f"CSMS not reachable at {url} (connection refused) - is the CSMS running on that address/port?"
    if name == "gaierror" or "getaddrinfo" in low or "11001" in text or "name or service not known" in low:
        return f"host name in {url} not found - check the IP address"
    if name in ("TimeoutError", "timeout") or "timed out" in low or "timeout" in low:
        return f"no answer from {url} (timeout) - check the IP address and the Windows Firewall on the CSMS PC"
    status = getattr(getattr(exc, "response", None), "status_code", None) or getattr(exc, "status_code", None)   # [VC-SEC]
    for code, why in ((401, "the CSMS refused the charger password (HTTP 401) - check the password and the security profile in the Connection box (CSMS: Registration tab -> Charger access)"),
                      (403, "the CSMS blocked this charger (HTTP 403) - ask the CSMS operator to allow it (Registration tab -> Charger access)"),
                      (404, "the CSMS does not know this charger id (HTTP 404) - add it in the CSMS (Registration tab -> Charger access) or check the URL path (/ocpp)"),
                      (429, "too many wrong passwords - the CSMS refuses this charger for a while (HTTP 429)")):
        if status == code or f"http {code}" in low:
            return why
    if name in ("InvalidStatus", "InvalidStatusCode", "InvalidHandshake") or "http 4" in low or "http 5" in low:
        return f"{url} answered but refused the WebSocket handshake ({text}) - check the URL path (/ocpp)"
    if "no route to host" in low or "unreachable" in low or "10065" in text or "10051" in text:
        return f"network unreachable for {url} - check the LAN / Wi-Fi connection"
    if "subprotocol" in low:
        return text
    if name.startswith("ConnectionClosed"):
        return f"connection closed by the CSMS ({text}) - reconnecting"
    return f"{name}: {text}"


def save_charger_config(url: str, cp_id: str, speed: float, auto_plug: bool = True, password: Optional[str] = None,
                        security_profile: Optional[int] = None) -> Optional[str]:
    """Persist the connection settings next to the program. Returns an error text or None.
    [VC-SEC] password / security_profile None = keep what the file has."""
    cfg_file = APP_DIR / CONFIG_FILE_NAME
    try:
        old = json.loads(cfg_file.read_text(encoding="utf-8")) if cfg_file.exists() else {}
    except (OSError, ValueError):
        old = {}
    if not isinstance(old, dict):
        old = {}
    data = {"url": url, "id": cp_id, "speed": speed, "auto_plug": bool(auto_plug)}
    pw = old.get("password", "") if password is None else password
    prof = old.get("security_profile", 0) if security_profile is None else security_profile
    if pw:
        data["password"] = pw
    if as_int(prof, 0):
        data["security_profile"] = as_int(prof, 0)
    try:
        cfg_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return None
    except OSError as exc:
        return f"could not save {cfg_file}: {exc}"


FLEET_KEYS_FILE = "vc_fleet_keys.json"   # [VC-SEC] passwords of the demo fleet stations on this PC


def load_fleet_keys() -> dict:
    try:
        data = json.loads((APP_DIR / FLEET_KEYS_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_fleet_keys(keys: dict) -> None:
    try:
        (APP_DIR / FLEET_KEYS_FILE).write_text(json.dumps(keys, indent=2), encoding="utf-8")
    except OSError as exc:
        LOG.warning("could not save %s: %s", FLEET_KEYS_FILE, exc)


def basic_auth_header(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")


def ws_header_kwargs(headers: dict) -> dict:
    """websockets >= 14 takes additional_headers, older versions extra_headers."""
    try:
        major = int(str(getattr(websockets, "__version__", "0")).split(".")[0])
    except ValueError:
        major = 0
    return {"additional_headers": headers} if major >= 14 else {"extra_headers": headers}


def mask_secret(detail: Any) -> Any:
    if isinstance(detail, dict) and str(detail.get("key", "")) in ("AuthorizationKey", "BasicAuthPassword") and detail.get("value"):
        return dict(detail, value="***")
    return detail


# ---------------------------------------------------------------------------
# Finding the CSMS without typing anything ("auto" mode)
#   1. csms_runtime.json written by a running Flexxfast CSMS in the same folder (same PC)
#   2. UDP broadcast on the LAN: every running Flexxfast CSMS answers with its ports
#   3. a quick scan of ports 9000-9009 on this PC
# ---------------------------------------------------------------------------
AUTO = "auto"
DISCOVERY_PORT = 9077
DISCOVERY_REQUEST = b"FLEXXFAST-CSMS-DISCOVER"
RUNTIME_FILE_NAME = "csms_runtime.json"


def is_auto(url: Any) -> bool:
    return str(url or "").strip().lower() in ("", AUTO)


def default_cp_id() -> str:
    """One identity per PC, so several simulators in a classroom do not collide on the CSMS."""
    import socket
    host = clean_cp_id(socket.gethostname().split(".")[0].upper().replace("_", "-"))
    return ("SIM-" + host)[:24] if host else "SIM-001"


def pid_alive(pid: int) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        try:
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:  # noqa: BLE001
            return True
    try:
        os.kill(int(pid), 0)       # POSIX only: signal 0 = existence check (on Windows it would kill!)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return True


def url_from_info(host: str, info: dict) -> str:
    scheme = "wss" if as_bool(info.get("tls", False)) else "ws"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{scheme}://{host}:{int(info['ocpp_port'])}{DEFAULT_OCPP_PATH}"


def read_runtime_file() -> Optional[dict]:
    """Ports of a CSMS running from the same folder (file is removed when it exits; stale if it was killed)."""
    try:
        info = json.loads((APP_DIR / RUNTIME_FILE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(info, dict) or info.get("service") != "flexxfast-csms" or not str(info.get("ocpp_port", "")).isdigit():
        return None
    if not pid_alive(as_int(info.get("pid"), 0)):
        return None
    return info


def local_ipv4_addresses() -> list[str]:
    import socket
    ips: set[str] = set()
    try:
        for entry in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(entry[4][0])
    except OSError:
        pass
    try:                                   # the address of the default route (no packet is sent)
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        ips.add(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    ips.discard("127.0.0.1")
    return sorted(ips)


def discover_csms_blocking(timeout: float = 1.5) -> list[dict]:
    """UDP request to this PC and the LAN; collects the replies of every Flexxfast CSMS (local ones first)."""
    import select
    import socket
    local_ips = local_ipv4_addresses()
    socks = []
    for bind_ip in [""] + local_ips:            # one socket per interface so the broadcast leaves on each of them
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setblocking(False)
            sock.bind((bind_ip, 0))
            socks.append(sock)
        except OSError:
            continue
    for sock in socks:
        for target in ("255.255.255.255", "127.0.0.1"):
            try:
                sock.sendto(DISCOVERY_REQUEST, (target, DISCOVERY_PORT))
            except OSError:
                pass
    found: dict[str, dict] = {}
    deadline = time.monotonic() + timeout
    while socks and time.monotonic() < deadline:
        try:
            readable, _, _ = select.select(socks, [], [], 0.2)
        except (OSError, ValueError):
            break
        for sock in readable:
            try:
                data, addr = sock.recvfrom(4096)
                info = json.loads(data.decode("utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(info, dict) or info.get("service") != "flexxfast-csms" or not str(info.get("ocpp_port", "")).isdigit():
                continue
            address = addr[0]
            key = f"{address}:{info['ocpp_port']}"
            if key not in found:
                found[key] = dict(info, address=address, local=address.startswith("127.") or address in local_ips)
    for sock in socks:
        sock.close()
    ordered = sorted(found.values(), key=lambda i: (not i["local"], not i["address"].startswith("127."), i["address"], int(i["ocpp_port"])))
    unique: list[dict] = []
    seen: set = set()
    for info in ordered:                        # the same CSMS process may answer on several of its addresses
        identity = (info.get("host"), info.get("pid"), int(info["ocpp_port"])) if info.get("host") and info.get("pid") else (info["address"], int(info["ocpp_port"]))
        if identity not in seen:
            seen.add(identity)
            unique.append(info)
    return unique


async def scan_localhost_ports(first: int = DEFAULT_OCPP_PORT, count: int = 10) -> list[int]:
    """Which of ports first..first+count-1 accept a TCP connection on this PC."""
    async def probe(port: int) -> Optional[int]:
        try:
            _reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 0.5)
        except Exception:  # noqa: BLE001
            return None
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        return port
    results = await asyncio.gather(*(probe(p) for p in range(first, first + count)))
    return [p for p in results if p]


# ---------------------------------------------------------------------------
# Configuration keys (what GetConfiguration reports / ChangeConfiguration edits)
# ---------------------------------------------------------------------------
def default_config(number_of_connectors: int, cp_id: str, url: str) -> dict[str, dict]:
    rw = lambda v: {"value": str(v), "readonly": False}   # noqa: E731
    ro = lambda v: {"value": str(v), "readonly": True}    # noqa: E731
    cfg = {
        "AuthorizeRemoteTxRequests": rw("true"),
        "ClockAlignedDataInterval": rw("0"),
        "ConnectionTimeOut": rw("60"),
        "GetConfigurationMaxKeys": ro("50"),
        "HeartbeatInterval": rw("300"),
        "LocalAuthorizeOffline": rw("true"),
        "LocalPreAuthorize": rw("false"),
        "MeterValuesAlignedData": rw("Energy.Active.Import.Register"),
        "MeterValuesSampledData": rw("Energy.Active.Import.Register,Power.Active.Import,Current.Import,Voltage,SoC"),
        "MeterValueSampleInterval": rw("10"),     # 10 s samples by default so the CSMS fleet view shows live power
        "NumberOfConnectors": ro(number_of_connectors),
        "ResetRetries": rw("3"),
        "ConnectorPhaseRotation": rw(",".join(f"{i}.RST" for i in range(number_of_connectors + 1))),
        "StopTransactionOnEVSideDisconnect": rw("true"),
        "StopTransactionOnInvalidId": rw("true"),
        "StopTxnAlignedData": rw("Energy.Active.Import.Register"),
        "StopTxnSampledData": rw("Energy.Active.Import.Register,Power.Active.Import"),
        "SupportedFeatureProfiles": ro("Core,FirmwareManagement,LocalAuthListManagement,Reservation,SmartCharging,RemoteTrigger"),
        "TransactionMessageAttempts": rw("3"),
        "TransactionMessageRetryInterval": rw("60"),
        "UnlockConnectorOnEVSideDisconnect": rw("true"),
        "AllowOfflineTxForUnknownId": rw("false"),
        "AuthorizationCacheEnabled": rw("true"),
        "AutoChargeEnabled": rw("true"),
        "LocalAuthListEnabled": rw("true"),
        "LocalAuthListMaxLength": ro("100"),
        "SendLocalListMaxLength": ro("50"),
        "MaxEnergyOnInvalidId": rw("0"),
        "MinimumStatusDuration": rw("0"),
        "WebSocketPingInterval": rw("60"),
        "ChargeProfileMaxStackLevel": ro("5"),
        "ChargingScheduleAllowedChargingRateUnit": ro("Current,Power"),
        "ChargingScheduleMaxPeriods": ro("24"),
        "MaxChargingProfilesInstalled": ro("10"),
        "ConnectorSwitch3to1PhaseSupported": ro("false"),
        "ReserveConnectorZeroSupported": ro("true"),
        "SupportedFileTransferProtocols": ro("HTTP,HTTPS,FTP"),
        "LightIntensity": rw("100"),
        "BlinkRepeat": rw("3"),
        "CentralSystemURL": rw(url),
        "ChargeBoxId": rw(cp_id),
        "SecurityProfile": rw("0"),       # [VC-SEC] 0 none, 1 Basic Auth, 2 Basic Auth + TLS (AuthorizationKey is write-only)
        "PaymentIdTagPrefix": ro("PAY-"),
        "AutoChargeIdTagPrefix": ro("AUTO-"),
    }
    for n in range(1, number_of_connectors + 1):
        cfg[f"EnableFrontButtons{n}"] = rw("true")
    return cfg


# ---------------------------------------------------------------------------
# Connector model
# ---------------------------------------------------------------------------
class Connector:
    def __init__(self, cid: int, max_power_w: float):
        self.id = cid
        self.max_power_w = max_power_w
        self.status = "Available"
        self.error_code = "NoError"
        self.vendor_error = ""
        self.plugged = False
        self.auto_plugged = False             # cable connected by the simulated driver (auto plug-in on remote start)
        self.full_since: Optional[float] = None   # battery at 100 % since (simulated driver leaves after a while)
        self.operative = True                 # ChangeAvailability
        self.pending_inoperative = False      # ChangeAvailability "Scheduled"
        self.auth: Optional[dict] = None      # {"idTag", "source", "expires", "reservationId"}
        self.finished = False                 # transaction ended, cable still plugged -> Finishing
        self.txn: Optional[dict] = None
        self.reservation: Optional[dict] = None
        self.meter_wh = float(random.randint(120_000, 980_000))   # lifetime energy register
        self.soc = 0.0
        self.power_w = 0.0
        self.voltage_v = 0.0
        self.current_a = 0.0
        self.temperature_c = 28.0 + random.random() * 4
        self.message = ""                     # text shown on the virtual display
        self.profiles: dict[int, dict] = {}   # chargingProfileId -> profile

    def to_dict(self) -> dict:
        t = self.txn
        return {
            "id": self.id, "status": self.status, "errorCode": self.error_code,
            "plugged": self.plugged, "operative": self.operative,
            "auth": {"idTag": self.auth["idTag"], "source": self.auth["source"]} if self.auth else None,
            "transaction": {
                "id": t["id"], "idTag": t["idTag"], "startTime": t["startTime"],
                "energyWh": round(t["energy_wh"], 1), "meterStart": t["meterStart"],
                "pending": t["id"] is None,
            } if t else None,
            "reservation": self.reservation,
            "meterWh": round(self.meter_wh, 1), "soc": round(self.soc, 1),
            "powerW": round(self.power_w), "voltageV": round(self.voltage_v, 1),
            "currentA": round(self.current_a, 1), "temperatureC": round(self.temperature_c, 1),
            "powerLimitW": round(self.power_limit_w()),
            "message": self.message,
        }

    def power_limit_w(self) -> float:
        """[VC-LM] OCPP 1.6 3.13: ChargePointMaxProfile caps everything; a TxProfile of the running transaction replaces
        the TxDefaultProfile; inside one purpose the highest stackLevel counts."""
        limit = self.max_power_w
        by: dict[str, list] = {}
        for prof in list(self.profiles.values()):
            by.setdefault(prof.get("chargingProfilePurpose") or "TxDefaultProfile", []).append(prof)
        for prof in by.get("ChargePointMaxProfile", []):
            w = profile_limit_w(prof)
            if w is not None:
                limit = min(limit, w)
        tid = self.txn.get("id") if self.txn else None
        tx = [p for p in by.get("TxProfile", []) if self.txn and p.get("transactionId") in (None, tid)]
        pool = tx or by.get("TxDefaultProfile", [])
        if pool:
            w = profile_limit_w(max(pool, key=lambda p: as_int(p.get("stackLevel"), 0)))
            if w is not None:
                limit = min(limit, w)
        return limit


def profile_limit_w(profile: dict) -> Optional[float]:
    sched = profile.get("chargingSchedule") or {}
    periods = sched.get("chargingSchedulePeriod") or []
    if not periods:
        return None
    try:
        limit = float(periods[0].get("limit", 0))
    except (TypeError, ValueError):
        return None
    if sched.get("chargingRateUnit") == "A":
        return limit * 400.0          # DC bus approximation: 400 V
    return limit


# ---------------------------------------------------------------------------
# The simulator
# ---------------------------------------------------------------------------
class VirtualCharger:
    def __init__(self, args):
        self.args = args
        self.cp_id: str = clean_cp_id(args.id) or default_cp_id()
        self.auto_url: bool = is_auto(args.url)              # find the CSMS by ourselves (no address typed)
        self.url: str = "" if self.auto_url else (normalize_csms_url(args.url, self.cp_id) or str(args.url).rstrip("/"))
        self.last_good_url: str = ""                         # where we last managed to connect (auto mode)
        self.discovered: list[dict] = []                     # CSMS instances seen by the last discovery round
        self._config_epoch = 0                               # bumped when the operator changes the settings
        self.vendor = args.vendor[:20]
        self.model = args.model[:20]
        self.serial = f"SIM{random.randint(100000, 999999)}"
        self.connectors = {n: Connector(n, args.max_kw * 1000.0) for n in range(1, args.connectors + 1)}
        self.cfg = default_config(args.connectors, self.cp_id, self.url or AUTO)
        self.speed = max(0.1, float(args.speed))
        self.auto_plug = bool(getattr(args, "auto_plug", True))   # simulated driver connects the cable after a remote start
        self.battery_kwh = 80.0
        self.auth_key: str = str(getattr(args, "password", "") or "")        # [VC-SEC] AuthorizationKey = Basic Auth password
        self.security_profile: int = max(0, min(2, as_int(getattr(args, "security_profile", None), 1 if self.auth_key else 0)))
        self.profile_fallback: Optional[int] = None                          # previous profile while a new one is tried
        if getattr(args, "fleet_member", False):
            saved_key = load_fleet_keys().get(self.cp_id) or {}
            if saved_key.get("key"):
                self.auth_key = str(saved_key["key"])
                self.security_profile = max(0, min(2, as_int(saved_key.get("profile"), 1)))
        if self.security_profile and not self.auth_key:
            self.security_profile = 0
        self.cfg["SecurityProfile"]["value"] = str(self.security_profile)

        self.ws = None
        self.online = False
        self.network_enabled = True
        self.last_error = ""                  # why the last connection attempt failed (shown on the panel)
        self.backoff = 2                      # seconds before the next connection attempt
        self._wake: Optional[asyncio.Event] = None   # set to retry immediately (settings changed)
        self.registration = "Unknown"         # BootNotification status
        self.boot_interval = 30
        self.heartbeat_interval = 300
        self.last_heartbeat = ""
        self.pending: dict[str, asyncio.Future] = {}
        self.offline_queue: list[tuple[str, dict]] = []
        self.auth_cache: dict[str, str] = {}
        self.local_list: dict[str, dict] = {}
        self.local_list_version = 0
        self.txn_counter = 0
        self.events: list[dict] = []          # OCPP traffic + simulator events for the panel
        self.diag_status = "Idle"
        self.fw_status = "Idle"
        self.firmware_version = FIRMWARE_VERSION      # [VC-FW] changes after a firmware update
        self.fw_installed_pending = False
        self.reboot_requested = False
        self.started = time.time()
        self._lock = asyncio.Lock()
        self.cp0_status = "Available"

    # ------------------------------------------------------------ helpers
    def cfg_get(self, key: str, default: str = "") -> str:
        return self.cfg.get(key, {}).get("value", default)

    def cfg_int(self, key: str, default: int = 0) -> int:
        return as_int(self.cfg_get(key), default)

    def log_event(self, kind: str, action: str, detail: Any = "") -> None:
        detail = mask_secret(detail)                                          # [VC-SEC]
        text = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False, default=str)
        entry = {"time": datetime.now().strftime("%H:%M:%S"), "kind": kind, "action": action, "detail": text[:600]}
        self.events.append(entry)
        if len(self.events) > 300:
            del self.events[:100]
        LOG.info("%-5s %-28s %s", kind, action, text[:300])

    def pay_page_url(self) -> str:
        """Customer "pay & charge" page of the connected CSMS (the QR sticker on a real charger)."""
        if not self.online or not self.url:
            return ""
        for d in self.discovered:                       # discovered CSMS: the web port is known
            if d.get("url") == self.url and d.get("web"):
                return f"{d['web'].rstrip('/')}/pay/{self.cp_id}"
        cloud = re.match(r"^(wss?)://([^/:]+|\[[^\]]+\])/", self.url + "/")
        if cloud:                                       # [VC-CLOUD] no port: OCPP shares the web server (cloud CSMS)
            return f"{'https' if cloud.group(1) == 'wss' else 'http'}://{cloud.group(2)}/pay/{self.cp_id}"
        m = re.match(r"^wss?://([^/]+?)(?::\d+)?/", self.url + "/")
        host = m.group(1) if m else "localhost"
        scheme = "https" if self.url.startswith("wss://") else "http"
        return f"{scheme}://{host}:8080/pay/{self.cp_id}"   # manual address: assume the default web port

    def state(self) -> dict:
        return {
            "cpId": self.cp_id, "url": self.url, "payUrl": self.pay_page_url(), "vendor": self.vendor, "model": self.model,
            "firmware": self.firmware_version, "online": self.online, "networkEnabled": self.network_enabled,
            "lastError": self.last_error, "speed": self.speed, "autoPlug": self.auto_plug, "configFile": CONFIG_FILE_NAME,
            "securityProfile": self.security_profile, "passwordSet": bool(self.auth_key),   # [VC-SEC]
            "autoUrl": self.auto_url, "discovered": self.discovered,
            "registration": self.registration, "heartbeatInterval": self.heartbeat_interval,
            "lastHeartbeat": self.last_heartbeat, "uptime": int(time.time() - self.started),
            "connectors": [c.to_dict() for c in self.connectors.values()],
            "config": {k: v["value"] for k, v in self.cfg.items()
                       if k in ("HeartbeatInterval", "MeterValueSampleInterval", "ClockAlignedDataInterval",
                                "AuthorizeRemoteTxRequests", "ConnectionTimeOut", "CentralSystemURL", "ChargeBoxId")
                       or k.startswith("EnableFrontButtons")},
            "diagStatus": self.diag_status, "fwStatus": self.fw_status,
            "queued": len(self.offline_queue), "authCache": sorted(self.auth_cache),
            "localList": {"version": self.local_list_version, "count": len(self.local_list)},
            "events": self.events[-80:],
        }

    # ------------------------------------------------------------ OCPP transport
    async def send_call(self, action: str, payload: dict, timeout: float = 30, queue_if_offline: bool = False) -> Optional[dict]:
        """Send a CALL and wait for CALLRESULT. Returns None if offline / timed out / error."""
        if not self.online or self.ws is None:
            if queue_if_offline:
                self.offline_queue.append((action, payload))
                self.offline_queue = self.offline_queue[-200:]
                self.log_event("QUEUE", action, "offline - queued for later")
            else:
                self.log_event("DROP", action, "offline - not sent")
            return None
        uid = uuid.uuid4().hex[:16]
        fut = asyncio.get_running_loop().create_future()
        self.pending[uid] = fut
        try:
            await self.ws.send(json.dumps([CALL, uid, action, payload], ensure_ascii=False))
            self.log_event("OUT", action, payload)
            result = await asyncio.wait_for(fut, timeout)
            self.log_event("IN", action + ".conf", result)
            return result
        except asyncio.TimeoutError:
            self.log_event("ERR", action, "timeout waiting for response")
        except ConnectionClosed:
            self.log_event("ERR", action, "connection closed")
            if queue_if_offline:
                self.offline_queue.append((action, payload))
        except Exception as exc:  # noqa: BLE001
            self.log_event("ERR", action, f"{type(exc).__name__}: {exc}")
        finally:
            self.pending.pop(uid, None)
        return None

    async def send_result(self, uid: str, payload: dict) -> None:
        if self.ws is not None:
            await self.ws.send(json.dumps([CALLRESULT, uid, payload], ensure_ascii=False))

    async def send_error(self, uid: str, code: str, desc: str) -> None:
        if self.ws is not None:
            await self.ws.send(json.dumps([CALLERROR, uid, code, desc, {}], ensure_ascii=False))

    def wake_event(self) -> asyncio.Event:
        if self._wake is None:
            self._wake = asyncio.Event()
        return self._wake

    async def _wait_before_retry(self, seconds: float) -> None:
        """Sleep between connection attempts, but return at once when settings change."""
        event = self.wake_event()
        try:
            await asyncio.wait_for(event.wait(), seconds)
        except asyncio.TimeoutError:
            pass
        event.clear()

    def retry_now(self) -> None:
        self.backoff = 2
        self.wake_event().set()

    async def resolve_candidates(self) -> list[str]:
        """Base URLs to try this round. Manual mode: the configured one. Auto mode: whatever can be found."""
        if not self.auto_url:
            return [self.url]
        cands: list[str] = []

        def add(url: str) -> None:
            if url and url not in cands:
                cands.append(url)

        info = read_runtime_file()                       # a CSMS started from this folder (same PC)
        if info:
            add(url_from_info("127.0.0.1", info))
        add(self.last_good_url)
        try:
            found = await asyncio.to_thread(discover_csms_blocking, 1.5)
        except Exception as exc:  # noqa: BLE001
            self.log_event("SYS", "Discovery", f"UDP discovery failed: {type(exc).__name__}: {exc}")
            found = []
        self.discovered = [{"url": url_from_info(i["address"], i), "host": str(i.get("host", "")),
                            "version": str(i.get("ocpp_version", "")), "local": bool(i.get("local")),
                            "web": f"{'https' if as_bool(i.get('tls', False)) else 'http'}://{i['address']}:{i.get('web_port', '')}/"}
                           for i in found]
        for entry in self.discovered:
            if entry["local"] or not getattr(self.args, "fleet_member", False):   # [VC-FLEET] fleet: this PC's CSMS only
                add(entry["url"])
        if not cands:                                     # nothing answered: look at the usual ports on this PC
            for port in await scan_localhost_ports():
                add(f"ws://127.0.0.1:{port}{DEFAULT_OCPP_PATH}")
        add(f"ws://127.0.0.1:{DEFAULT_OCPP_PORT}{DEFAULT_OCPP_PATH}")
        return cands

    async def connection_loop(self) -> None:
        self.backoff = 2
        while True:
            if not self.network_enabled:
                await asyncio.sleep(1)
                continue
            epoch = self._config_epoch
            if self.auto_url:
                self.last_error = self.last_error or ("looking for the Flexxfast CSMS on this PC ..."      # [VC-FLEET]
                                                      if getattr(self.args, "fleet_member", False)
                                                      else "looking for a Flexxfast CSMS on this PC / in this LAN ...")
            candidates = await self.resolve_candidates()
            had_session = False
            for base in candidates:
                if not self.network_enabled or epoch != self._config_epoch:
                    break
                self.url = base
                url = f"{base}/{self.cp_id}"
                self.log_event("SYS", "Connecting", url)
                connect_kwargs: dict[str, Any] = {}
                if self.security_profile >= 2 and not base.lower().startswith("wss://"):     # [VC-SEC]
                    self.last_error = "security profile 2 needs a wss:// (TLS) CSMS address - change the address or use profile 1"
                    self.log_event("SYS", "Security", self.last_error)
                    continue
                if self.security_profile >= 1 and self.auth_key:
                    connect_kwargs.update(ws_header_kwargs({"Authorization": basic_auth_header(self.cp_id, self.auth_key)}))
                if base.lower().startswith("wss://"):
                    import ssl
                    connect_kwargs["ssl"] = ssl._create_unverified_context()    # simulator: accept self-signed CSMS certs
                try:
                    async with websockets.connect(url, subprotocols=["ocpp1.6"], ping_interval=20, ping_timeout=20,
                                                  max_size=2 ** 20, open_timeout=10, **connect_kwargs) as ws:
                        if ws.subprotocol != "ocpp1.6":
                            self.log_event("SYS", "Rejected", f"CSMS did not accept subprotocol ocpp1.6 (got {ws.subprotocol!r}) - is the CSMS running in OCPP 2.0.1 mode?")
                            await ws.close()
                            raise ConnectionError("subprotocol mismatch - the CSMS is in OCPP 2.0.1 mode (switch it to 1.6 in the CSMS Web UI)")
                        self.ws = ws
                        self.online = True
                        had_session = True
                        self.backoff = 2
                        self.last_error = ""
                        self.last_good_url = base
                        self.cfg.setdefault("CentralSystemURL", {"value": base, "readonly": False})["value"] = base
                        self.log_event("SYS", "Connected", url + (f" (security profile {self.security_profile})" if self.security_profile else ""))
                        if self.profile_fallback is not None:                        # [VC-SEC] new profile works
                            self.profile_fallback = None
                        session = asyncio.create_task(self.session_tasks())
                        try:
                            async for raw in ws:
                                await self.on_message(raw)
                        finally:
                            session.cancel()
                        if not self.last_error:          # not closed by the operator (network cut / reboot / settings)
                            self.last_error = "connection closed by the CSMS - reconnecting"
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    self.last_error = describe_connect_error(exc, url)
                    self.log_event("SYS", "Disconnected", f"{type(exc).__name__}: {exc}")
                    if self.profile_fallback is not None and "401" in str(exc):          # [VC-SEC] back to the old profile
                        self.log_event("SYS", "Security", f"profile {self.security_profile} refused by the CSMS - back to profile {self.profile_fallback}")
                        self.security_profile, self.profile_fallback = self.profile_fallback, None
                        self.cfg["SecurityProfile"]["value"] = str(self.security_profile)
                        self.save_security()
                finally:
                    self.ws = None
                    self.online = False
                    for fut in self.pending.values():
                        if not fut.done():
                            fut.set_exception(ConnectionError("disconnected"))
                    self.pending.clear()
                if had_session or self.reboot_requested:
                    break
            if self.auto_url and not had_session and epoch == self._config_epoch and self.network_enabled:
                self.last_error = ("no Flexxfast CSMS found yet (tried " + ", ".join(candidates) +
                                   ") - start the CSMS on this PC or in this LAN, or type its address in the Connection box")
                if getattr(self.args, "fleet_member", False):    # [VC-FLEET]
                    self.last_error = ("Flexxfast CSMS is not running on this PC yet - start it (desktop icon "
                                       "\"Flexxfast CSMS\"); the demo fleet connects by itself")
            if self.reboot_requested:
                self.reboot_requested = False
                self.log_event("SYS", "Rebooting", "simulated reboot (3 s)")
                await asyncio.sleep(3)
                self.backoff = 2
                continue
            await self._wait_before_retry(self.backoff)
            self.backoff = min(self.backoff * 2, 30)

    async def session_tasks(self) -> None:
        """Runs while connected: BootNotification handshake, heartbeats, queue flush."""
        try:
            await self.boot_sequence()
            while True:
                await asyncio.sleep(max(5, self.heartbeat_interval))
                if self.registration == "Accepted":
                    await self.heartbeat()
        except asyncio.CancelledError:
            pass

    async def boot_sequence(self) -> None:
        self.registration = "Unknown"
        while self.online:
            if self.registration != "Accepted":
                await self.boot_notification()
            if self.registration == "Accepted":
                return
            # Pending / Rejected / no answer: retry after the interval the CSMS gave us
            # (the CSMS may also TriggerMessage(BootNotification) earlier - that is handled too)
            wait = max(5, self.boot_interval)
            self.log_event("SYS", "BootRetry", f"registration={self.registration}, retry in {wait}s")
            await asyncio.sleep(wait)

    async def boot_notification(self) -> bool:
        payload = {"chargePointVendor": self.vendor, "chargePointModel": self.model,
                   "chargePointSerialNumber": self.serial, "firmwareVersion": self.firmware_version,
                   "meterType": "SIM-DC-METER", "meterSerialNumber": "SIMMTR001"}
        result = await self.send_call("BootNotification", payload)
        if not result:
            return False
        status = str(result.get("status", "Rejected"))
        self.boot_interval = as_int(result.get("interval"), 60)
        prev = self.registration
        self.registration = status
        if status == "Accepted":
            self.heartbeat_interval = self.boot_interval or self.cfg_int("HeartbeatInterval", 300)
            self.cfg["HeartbeatInterval"]["value"] = str(self.heartbeat_interval)
            if prev != "Accepted":
                await self.send_all_status()
                await self.flush_queue()
            if self.fw_installed_pending:                  # [VC-FW] started with the new firmware
                self.fw_installed_pending = False
                self.fw_status = "Installed"
                await self.send_call("FirmwareStatusNotification", {"status": "Installed"})
        return True

    async def heartbeat(self) -> None:
        result = await self.send_call("Heartbeat", {})
        if result:
            self.last_heartbeat = datetime.now().strftime("%H:%M:%S")

    async def flush_queue(self) -> None:
        queued, self.offline_queue = self.offline_queue, []
        for action, payload in queued:
            result = await self.send_call(action, payload, queue_if_offline=True)
            if action == "StartTransaction" and result:
                self._apply_start_conf(payload.get("connectorId"), result)

    # ------------------------------------------------------------ incoming CALLs from CSMS
    async def on_message(self, raw: str) -> None:
        try:
            frame = json.loads(raw)
        except json.JSONDecodeError:
            self.log_event("ERR", "Frame", "invalid JSON from CSMS")
            return
        if not isinstance(frame, list) or len(frame) < 3:
            return
        mtype, uid = frame[0], str(frame[1])
        if mtype == CALLRESULT:
            fut = self.pending.get(uid)
            if fut and not fut.done():
                fut.set_result(frame[2] if isinstance(frame[2], dict) else {})
            return
        if mtype == CALLERROR:
            fut = self.pending.get(uid)
            if fut and not fut.done():
                fut.set_exception(RuntimeError(f"CALLERROR {frame[2]}: {frame[3] if len(frame) > 3 else ''}"))
            return
        if mtype != CALL or len(frame) < 4:
            return
        action, payload = str(frame[2]), frame[3] if isinstance(frame[3], dict) else {}
        self.log_event("IN", action, payload)
        # handle each CSMS call in its own task so the reader keeps consuming CALLRESULTs
        # (handlers such as UnlockConnector send their own calls before answering)
        asyncio.create_task(self.handle_call(uid, action, payload))

    async def handle_call(self, uid: str, action: str, payload: dict) -> None:
        handler = getattr(self, "on_" + camel_to_snake(action), None)
        if handler is None:
            await self.send_error(uid, "NotImplemented", f"{action} not supported by simulator")
            self.log_event("OUT", action + ".error", "NotImplemented")
            return
        try:
            result = await handler(payload)
        except Exception as exc:  # noqa: BLE001
            LOG.exception("handler %s failed", action)
            await self.send_error(uid, "InternalError", str(exc)[:200])
            return
        if isinstance(result, tuple):       # (response, coroutine to run after answering)
            result, after = result
        else:
            after = None
        await self.send_result(uid, result)
        self.log_event("OUT", action + ".conf", result)
        if after is not None:
            asyncio.create_task(after)

    # --- Core
    async def on_remote_start_transaction(self, p: dict):
        id_tag = str(p.get("idTag", ""))[:20]
        cid = as_int(p.get("connectorId"), 0)
        conn = self.connectors.get(cid) if cid else self.pick_connector_for_remote_start()
        if conn is None or not id_tag or conn.txn or conn.status in ("Faulted", "Unavailable"):
            return {"status": "Rejected"}
        if conn.reservation and conn.reservation["idTag"].upper() != id_tag.upper():
            return {"status": "Rejected"}
        if p.get("chargingProfile"):
            conn.profiles[int(p["chargingProfile"].get("chargingProfileId", 0))] = p["chargingProfile"]
        return {"status": "Accepted"}, self.remote_start_flow(conn, id_tag)

    def pick_connector_for_remote_start(self) -> Optional[Connector]:
        free = [c for c in self.connectors.values() if not c.txn and c.status not in ("Faulted", "Unavailable")]
        plugged = [c for c in free if c.plugged]
        return (plugged or free or [None])[0]

    async def remote_start_flow(self, conn: Connector, id_tag: str) -> None:
        if as_bool(self.cfg_get("AuthorizeRemoteTxRequests", "true")):
            ok = await self.authorize(conn, id_tag, "remote")
            if not ok:
                return
        else:
            self.set_auth(conn, id_tag, "remote")
        await self.try_start(conn)
        if conn.auth and not conn.plugged and not conn.txn and self.auto_plug:
            # a real charger now waits for the driver to connect the cable (ConnectionTimeOut); in the simulator the
            # "driver" does it a few seconds later so a payment from the app / pay page runs through the whole loop
            conn.message = "Remote start - the driver is connecting the cable ..."
            asyncio.create_task(self.simulated_plug_in(conn))

    async def simulated_plug_in(self, conn: Connector, delay: float = 3.0) -> None:
        await asyncio.sleep(delay)
        if conn.plugged or conn.txn or not conn.auth or not self.auto_plug:
            return
        conn.plugged = True
        conn.auto_plugged = True
        conn.finished = False
        conn.full_since = None
        conn.soc = random.uniform(10, 20)     # arrives fairly empty so a prepaid budget is normally reached before 100 %
        conn.message = "Cable connected (simulated driver)"
        self.log_event("SYS", "AutoPlug", f"C{conn.id} cable connected by the simulated driver (remote start)")
        await self.update_status(conn)
        await self.try_start(conn)

    async def simulated_leave(self, conn: Connector) -> None:
        """Battery full while the budget is not used up: the simulated driver unplugs (StopTransaction EVDisconnected),
        the CSMS settles the session and refunds the unused amount."""
        if not conn.txn or not conn.auto_plugged:
            return
        self.log_event("SYS", "AutoPlug", f"C{conn.id} battery full - the simulated driver unplugs")
        if as_bool(self.cfg_get("StopTransactionOnEVSideDisconnect", "true")):
            await self.stop_transaction(conn, "EVDisconnected")
        else:
            conn.txn["suspended_ev"] = True
        conn.plugged = False
        conn.auto_plugged = False
        conn.auth = None
        conn.finished = False
        conn.full_since = None
        conn.message = "Battery full - cable removed (simulated driver) - ready for the next customer"
        await self.update_status(conn)

    async def simulated_unplug(self, conn: Connector, delay: float = 6.0) -> None:
        await asyncio.sleep(delay)
        if conn.txn or not conn.auto_plugged or not conn.plugged:
            return
        conn.plugged = False
        conn.auto_plugged = False
        conn.auth = None
        conn.finished = False
        conn.message = "Cable removed (simulated driver) - ready for the next customer"
        self.log_event("SYS", "AutoPlug", f"C{conn.id} cable removed by the simulated driver")
        await self.update_status(conn)

    async def on_remote_stop_transaction(self, p: dict):
        tid = as_int(p.get("transactionId"), -1)
        for conn in self.connectors.values():
            if conn.txn and conn.txn.get("id") == tid:
                return {"status": "Accepted"}, self.stop_transaction(conn, "Remote")
        return {"status": "Rejected"}

    async def on_reset(self, p: dict):
        kind = str(p.get("type", "Soft"))
        return {"status": "Accepted"}, self.reboot(f"{kind}Reset")

    async def reboot(self, reason: str) -> None:
        await asyncio.sleep(0.5)
        for conn in self.connectors.values():
            if conn.txn:
                await self.stop_transaction(conn, reason)
        # a reboot picks up a changed CentralSystemURL / ChargeBoxId (set by the CSMS via ChangeConfiguration)
        new_url = self.cfg_get("CentralSystemURL", self.url).rstrip("/")
        new_id = clean_cp_id(self.cfg_get("ChargeBoxId", self.cp_id)) or self.cp_id
        if is_auto(new_url):
            if not self.auto_url:
                self.log_event("SYS", "Reboot", "CentralSystemURL = auto -> will search for the CSMS")
            self.auto_url, self.url = True, ""
        elif new_url != self.url:
            self.log_event("SYS", "Reboot", f"switching to {new_url}")
            self.auto_url, self.url = False, new_url
        if new_id != self.cp_id:
            self.log_event("SYS", "Reboot", f"charge point id is now {new_id}")
            self.cp_id = new_id
        self.last_error = ""
        self.reboot_requested = True
        if self.ws is not None:
            await self.ws.close(code=1001, reason="reboot")

    async def on_unlock_connector(self, p: dict):
        conn = self.connectors.get(as_int(p.get("connectorId"), 0))
        if conn is None:
            return {"status": "NotSupported"}
        if conn.txn:
            await self.stop_transaction(conn, "UnlockCommand")
            await asyncio.sleep(1.0)
        conn.plugged = False
        conn.finished = False
        conn.message = "Connector unlocked"
        await self.update_status(conn)
        return {"status": "Unlocked"}

    async def on_change_availability(self, p: dict):
        cid = as_int(p.get("connectorId"), 0)
        operative = str(p.get("type")) == "Operative"
        targets = list(self.connectors.values()) if cid == 0 else [self.connectors.get(cid)]
        if any(t is None for t in targets):
            return {"status": "Rejected"}
        scheduled = False
        for conn in targets:
            if not operative and conn.txn:
                conn.pending_inoperative = True
                scheduled = True
            else:
                conn.operative = operative
                conn.pending_inoperative = False
        if cid == 0:
            self.cp0_status = "Available" if operative else "Unavailable"
        return {"status": "Scheduled" if scheduled else "Accepted"}, self.after_availability(cid)

    async def after_availability(self, cid: int) -> None:
        if cid == 0 and self.registration == "Accepted":
            await self.send_status(0, self.cp0_status)
        await self.refresh_all_status()

    async def on_get_configuration(self, p: dict):
        keys = p.get("key") or []
        keys = [k for k in keys if isinstance(k, str) and k.strip()]
        known, unknown = [], []
        if not keys:
            known = [{"key": k, "readonly": v["readonly"], "value": v["value"]} for k, v in self.cfg.items()]
        else:
            for k in keys:
                if k in self.cfg:
                    known.append({"key": k, "readonly": self.cfg[k]["readonly"], "value": self.cfg[k]["value"]})
                else:
                    unknown.append(k)
        result = {"configurationKey": known}
        if unknown:
            result["unknownKey"] = unknown
        return result

    def save_security(self) -> None:                                           # [VC-SEC]
        if getattr(self.args, "fleet_member", False):
            keys = load_fleet_keys()
            keys[self.cp_id] = {"key": self.auth_key, "profile": self.security_profile}
            save_fleet_keys(keys)
        else:
            save_charger_config(AUTO if self.auto_url else self.url, self.cp_id, self.speed, self.auto_plug,
                                self.auth_key, self.security_profile)

    async def reconnect_soon(self, reason: str) -> None:
        await asyncio.sleep(1.0)
        self.last_error = reason + " - reconnecting"
        if self.ws is not None:
            await self.ws.close(code=1001, reason=reason[:60])

    def change_security(self, key: str, value: str):
        """OCPP 1.6 security whitepaper: AuthorizationKey (write-only) and SecurityProfile (no downgrade)."""
        if key == "AuthorizationKey":
            if not (16 <= len(value) <= 40) or any(ord(ch) < 33 or ord(ch) > 126 for ch in value):
                return {"status": "Rejected"}
            self.auth_key = value
            self.save_security()
            self.log_event("SYS", "Security", "new AuthorizationKey stored (used from the next connection)")
            return {"status": "Accepted"}
        p = as_int(value, -1)
        if p < 0 or p > 3 or p == 3 or p < self.security_profile:
            return {"status": "Rejected"}
        if p >= 1 and not self.auth_key:
            return {"status": "Rejected"}
        if p == 2 and not (self.url or "").lower().startswith("wss://"):
            return {"status": "Rejected"}
        if p == self.security_profile:
            return {"status": "Accepted"}
        self.profile_fallback, self.security_profile = self.security_profile, p
        self.cfg["SecurityProfile"]["value"] = str(p)
        self.save_security()
        self.log_event("SYS", "Security", f"security profile {self.profile_fallback} -> {p}: reconnecting")
        return {"status": "Accepted"}, self.reconnect_soon("security profile changed")

    async def on_change_configuration(self, p: dict):
        key, value = str(p.get("key", "")), str(p.get("value", ""))
        if key in ("AuthorizationKey", "SecurityProfile"):                       # [VC-SEC]
            return self.change_security(key, value)
        if key not in self.cfg:
            return {"status": "NotSupported"}
        if self.cfg[key]["readonly"]:
            return {"status": "Rejected"}
        if key in ("HeartbeatInterval", "MeterValueSampleInterval", "ClockAlignedDataInterval", "ConnectionTimeOut",
                   "LightIntensity", "BlinkRepeat", "MinimumStatusDuration", "WebSocketPingInterval",
                   "TransactionMessageAttempts", "TransactionMessageRetryInterval", "ResetRetries", "MaxEnergyOnInvalidId"):
            if as_int(value, -1) < 0:
                return {"status": "Rejected"}
        self.cfg[key]["value"] = value
        if key == "HeartbeatInterval":
            self.heartbeat_interval = as_int(value, 300)
        if key in ("MeterValueSampleInterval", "ClockAlignedDataInterval"):
            for conn in self.connectors.values():
                if conn.txn:
                    conn.txn["next_periodic"] = None
                    conn.txn["next_clock"] = None
        if key.startswith("EnableFrontButtons"):
            n = as_int(key.replace("EnableFrontButtons", ""), 0)
            conn = self.connectors.get(n)
            if conn:
                conn.message = f"Front buttons {'enabled' if as_bool(value) else 'disabled'} by CSMS"
        if key in ("CentralSystemURL", "ChargeBoxId"):
            return {"status": "RebootRequired"}
        return {"status": "Accepted"}

    async def on_clear_cache(self, p: dict):
        self.auth_cache.clear()
        return {"status": "Accepted"}

    async def on_data_transfer(self, p: dict):
        vendor = str(p.get("vendorId", ""))
        message = str(p.get("messageId", ""))
        if vendor not in ("PEA", "Flexxfast", "Flexxfast by EDS", "EDS"):
            return {"status": "UnknownVendorId"}
        if message == "Ping":
            return {"status": "Accepted", "data": "Pong"}
        if message == "GetVersion":
            return {"status": "Accepted", "data": json.dumps({"firmware": self.firmware_version, "ocpp": "1.6J", "model": self.model})}
        if message == "Accepted":
            return {"status": "Accepted"}
        if message == "Rejected":
            return {"status": "Rejected"}
        if message == "SwitchOcppVersion":
            self.log_event("SYS", "SwitchOcppVersion", f"{p.get('data')} - simulator only speaks OCPP 1.6; it will keep retrying with ocpp1.6")
            return {"status": "Accepted"}
        return {"status": "UnknownMessageId"}

    # --- Remote trigger
    async def on_trigger_message(self, p: dict):
        msg = str(p.get("requestedMessage", ""))
        cid = p.get("connectorId")
        if msg == "BootNotification":
            return {"status": "Accepted"}, self.boot_notification()
        if msg == "Heartbeat":
            return {"status": "Accepted"}, self.heartbeat()
        if msg == "StatusNotification":
            return {"status": "Accepted"}, self.send_all_status(as_int(cid, 0) if cid is not None else None)
        if msg == "MeterValues":
            targets = [self.connectors[as_int(cid, 0)]] if cid and as_int(cid, 0) in self.connectors else list(self.connectors.values())
            return {"status": "Accepted"}, self.send_meter_values(targets, "Trigger")
        if msg == "DiagnosticsStatusNotification":
            return {"status": "Accepted"}, self.send_call("DiagnosticsStatusNotification", {"status": self.diag_status})
        if msg == "FirmwareStatusNotification":
            return {"status": "Accepted"}, self.send_call("FirmwareStatusNotification", {"status": self.fw_status})
        return {"status": "NotImplemented"}

    # --- Reservation
    async def on_reserve_now(self, p: dict):
        cid = as_int(p.get("connectorId"), -1)
        expiry = parse_iso(p.get("expiryDate"))
        id_tag = str(p.get("idTag", ""))[:20]
        rid = as_int(p.get("reservationId"), 0)
        if expiry is None or expiry <= utc_now() or not id_tag:
            return {"status": "Rejected"}
        targets = list(self.connectors.values()) if cid == 0 else [self.connectors.get(cid)]
        if any(t is None for t in targets):
            return {"status": "Rejected"}
        for conn in targets:
            if conn.status == "Faulted":
                return {"status": "Faulted"}
            if not conn.operative:
                return {"status": "Unavailable"}
            if conn.txn or (conn.reservation and conn.reservation["id"] != rid):
                return {"status": "Occupied"}
        for conn in targets:
            conn.reservation = {"id": rid, "idTag": id_tag, "expiry": iso(expiry), "connectorId": cid}
            conn.message = f"Reserved for {id_tag}"
        return {"status": "Accepted"}, self.refresh_all_status()

    async def on_cancel_reservation(self, p: dict):
        rid = as_int(p.get("reservationId"), -1)
        found = False
        for conn in self.connectors.values():
            if conn.reservation and conn.reservation["id"] == rid:
                conn.reservation = None
                conn.message = "Reservation cancelled"
                found = True
        if not found:
            return {"status": "Rejected"}
        return {"status": "Accepted"}, self.refresh_all_status()

    # --- Smart charging
    async def on_set_charging_profile(self, p: dict):
        cid = as_int(p.get("connectorId"), -1)
        prof = p.get("csChargingProfiles") or {}
        pid = as_int(prof.get("chargingProfileId"), 0)
        if cid < 0 or not prof or (cid and cid not in self.connectors):
            return {"status": "Rejected"}
        targets = list(self.connectors.values()) if cid == 0 else [self.connectors[cid]]
        for conn in targets:
            conn.profiles[pid] = prof
            conn.message = f"Power limit {conn.power_limit_w() / 1000:.1f} kW"
        return {"status": "Accepted"}

    async def on_clear_charging_profile(self, p: dict):
        pid = p.get("id")
        cid = p.get("connectorId")
        purpose = p.get("chargingProfilePurpose")
        stack = p.get("stackLevel")
        cleared = 0
        for conn in self.connectors.values():
            if cid not in (None, 0) and conn.id != as_int(cid, -1):
                continue
            for key, prof in list(conn.profiles.items()):
                if pid is not None and key != as_int(pid, -1):
                    continue
                if purpose and prof.get("chargingProfilePurpose") != purpose:
                    continue
                if stack is not None and as_int(prof.get("stackLevel"), -1) != as_int(stack, -2):
                    continue
                del conn.profiles[key]
                cleared += 1
        return {"status": "Accepted" if cleared else "Unknown"}

    async def on_get_composite_schedule(self, p: dict):
        cid = as_int(p.get("connectorId"), 0)
        duration = as_int(p.get("duration"), 3600)
        unit = p.get("chargingRateUnit") or "W"
        conns = list(self.connectors.values()) if cid == 0 else [self.connectors.get(cid)]
        if any(c is None for c in conns):
            return {"status": "Rejected"}
        limit_w = sum(c.power_limit_w() for c in conns)
        limit = limit_w / 400.0 if unit == "A" else limit_w
        return {"status": "Accepted", "connectorId": cid, "scheduleStart": iso(),
                "chargingSchedule": {"duration": duration, "startSchedule": iso(), "chargingRateUnit": unit,
                                     "chargingSchedulePeriod": [{"startPeriod": 0, "limit": round(limit, 1)}]}}

    # --- Local auth list
    async def on_get_local_list_version(self, p: dict):
        return {"listVersion": self.local_list_version}

    async def on_send_local_list(self, p: dict):
        version = as_int(p.get("listVersion"), 0)
        update = str(p.get("updateType", "Full"))
        entries = p.get("localAuthorizationList") or []
        if update == "Differential" and version <= self.local_list_version:
            return {"status": "VersionMismatch"}
        if update == "Full":
            self.local_list = {}
        for item in entries:
            tag = str(item.get("idTag", "")).upper()
            if not tag:
                continue
            info = item.get("idTagInfo")
            if info is None:
                self.local_list.pop(tag, None)
            else:
                self.local_list[tag] = info
        self.local_list_version = version
        return {"status": "Accepted"}

    # --- Firmware / diagnostics
    async def on_get_diagnostics(self, p: dict):
        location = str(p.get("location", ""))
        if not location:
            return {}
        name = f"{self.cp_id}_diag_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        return {"fileName": name}, self.upload_diagnostics(location, name)

    async def upload_diagnostics(self, location: str, name: str) -> None:
        self.diag_status = "Uploading"
        await self.send_call("DiagnosticsStatusNotification", {"status": "Uploading"})
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("simulator_info.txt", f"Flexxfast virtual charger {self.cp_id}\nfirmware {self.firmware_version}\n"
                                              f"generated {iso()}\nconnectors {len(self.connectors)}\n")
            zf.writestr("ocpp_events.log", "\n".join(f"{e['time']} {e['kind']} {e['action']} {e['detail']}" for e in self.events[-300:]))
            zf.writestr("config.json", json.dumps({k: v["value"] for k, v in self.cfg.items()}, indent=2))
        url = location if location.endswith(name) else location.rstrip("/") + "/" + name
        status = "UploadFailed"
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.put(url, data=buf.getvalue(), headers={"Content-Type": "application/zip"}) as resp:
                    self.log_event("SYS", "DiagnosticsUpload", f"PUT {url} -> HTTP {resp.status}")
                    if 200 <= resp.status < 300:
                        status = "Uploaded"
        except Exception as exc:  # noqa: BLE001
            self.log_event("ERR", "DiagnosticsUpload", f"{type(exc).__name__}: {exc}")
        self.diag_status = status
        await self.send_call("DiagnosticsStatusNotification", {"status": status})

    async def on_update_firmware(self, p: dict):
        return {}, self.firmware_update(str(p.get("location", "")), str(p.get("retrieveDate", "")),
                                        as_int(p.get("retries"), 1), as_int(p.get("retryInterval"), 30))

    async def send_fw_status(self, status: str) -> None:
        self.fw_status = status
        await self.send_call("FirmwareStatusNotification", {"status": status})

    async def firmware_update(self, location: str, retrieve: str, retries: int, interval: int) -> None:
        """[VC-FW] download the file for real, 'install' it (version from the file name) and restart."""
        try:
            when = datetime.fromisoformat(retrieve.replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            wait = (when - datetime.now(timezone.utc)).total_seconds()
            if wait > 0:
                self.log_event("SYS", "Firmware", f"download at {retrieve} (in {int(wait)} s)")
                await asyncio.sleep(min(wait, 6 * 3600))
        except ValueError:
            pass
        data = None
        for attempt in range(max(1, retries)):
            await self.send_fw_status("Downloading")
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600, sock_connect=20)) as session:
                    async with session.get(location) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            self.log_event("SYS", "Firmware", f"downloaded {len(data):,} bytes from {location}")
                            break
                        self.log_event("ERR", "Firmware", f"GET {location} -> HTTP {resp.status}")
            except Exception as exc:  # noqa: BLE001
                self.log_event("ERR", "Firmware", f"{type(exc).__name__}: {exc}")
            if attempt + 1 < max(1, retries):
                await asyncio.sleep(max(1, min(interval, 300)))
        if data is None:
            await self.send_fw_status("DownloadFailed")
            return
        await self.send_fw_status("Downloaded")
        await asyncio.sleep(1)
        await self.send_fw_status("Installing")
        await asyncio.sleep(2)
        name = location.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        m = re.search(r"(\d+(?:\.\d+){1,3})", name)
        self.firmware_version = "SIM-" + (m.group(1) if m else datetime.now().strftime("%Y.%m.%d"))
        self.fw_installed_pending = True
        self.log_event("SYS", "Firmware", f"installed {name} -> {self.firmware_version}, restarting")
        await self.reboot("FirmwareUpdate")

    # ------------------------------------------------------------ status notifications
    async def send_status(self, cid: int, status: str, error_code: str = "NoError", vendor_error: str = "") -> None:
        payload = {"connectorId": cid, "errorCode": error_code, "status": status, "timestamp": iso()}
        if vendor_error:
            payload["vendorErrorCode"] = vendor_error[:50]
        await self.send_call("StatusNotification", payload)

    async def send_all_status(self, only: Optional[int] = None) -> None:
        if only is None or only == 0:
            await self.send_status(0, self.cp0_status)
        for conn in self.connectors.values():
            if only is None or only == 0 or only == conn.id:
                await self.send_status(conn.id, conn.status, conn.error_code, conn.vendor_error)

    def compute_status(self, conn: Connector) -> str:
        if conn.error_code != "NoError":
            return "Faulted"
        if not conn.operative:
            return "Unavailable"
        if conn.txn:
            if conn.txn.get("finishing"):
                return "Finishing"
            if conn.soc >= 100 or conn.txn.get("suspended_ev"):
                return "SuspendedEV"
            if conn.power_limit_w() <= 0:
                return "SuspendedEVSE"
            return "Charging"
        if conn.reservation:
            return "Reserved"
        if conn.auth:
            return "Preparing"
        if conn.plugged:
            return "Finishing" if conn.finished else "Preparing"
        return "Available"

    async def update_status(self, conn: Connector) -> None:
        new = self.compute_status(conn)
        if new != conn.status:
            conn.status = new
            if self.registration == "Accepted":
                await self.send_status(conn.id, new, conn.error_code, conn.vendor_error)

    async def refresh_all_status(self) -> None:
        for conn in self.connectors.values():
            await self.update_status(conn)

    # ------------------------------------------------------------ authorization & transactions
    def set_auth(self, conn: Connector, id_tag: str, source: str) -> None:
        timeout = max(5, self.cfg_int("ConnectionTimeOut", 60))
        conn.auth = {"idTag": id_tag, "source": source, "expires": time.time() + timeout}
        conn.message = f"Authorized ({source}) - plug in within {timeout}s" if not conn.plugged else f"Authorized ({source})"

    async def authorize(self, conn: Connector, id_tag: str, source: str) -> bool:
        id_tag = id_tag[:20]
        if conn.reservation and conn.reservation["idTag"].upper() != id_tag.upper():
            conn.message = "Connector reserved for another card"
            return False
        status = None
        if not self.online or self.registration != "Accepted":
            # offline: local list / cache
            if as_bool(self.cfg_get("LocalAuthorizeOffline", "true")):
                info = self.local_list.get(id_tag.upper())
                if info and info.get("status") == "Accepted":
                    status = "Accepted"
                elif self.auth_cache.get(id_tag.upper()) == "Accepted":
                    status = "Accepted"
            if status is None:
                conn.message = "Offline - card not in local list"
                return False
        else:
            result = await self.send_call("Authorize", {"idTag": id_tag})
            if not result:
                conn.message = "No answer from CSMS"
                return False
            status = str((result.get("idTagInfo") or {}).get("status", "Invalid"))
            if as_bool(self.cfg_get("AuthorizationCacheEnabled", "true")):
                self.auth_cache[id_tag.upper()] = status
        if status != "Accepted":
            conn.message = f"Card {id_tag}: {status}"
            self.log_event("SYS", "AuthorizeDenied", f"C{conn.id} {id_tag} -> {status}")
            await self.update_status(conn)
            return False
        conn.finished = False
        self.set_auth(conn, id_tag, source)
        await self.update_status(conn)
        return True

    async def try_start(self, conn: Connector) -> None:
        if conn.auth and conn.plugged and not conn.txn and conn.operative and conn.error_code == "NoError":
            await self.start_transaction(conn)
        else:
            await self.update_status(conn)

    async def start_transaction(self, conn: Connector) -> None:
        auth = conn.auth or {}
        id_tag = auth.get("idTag", "UNKNOWN")[:20]
        conn.auth = None
        meter_start = int(conn.meter_wh)
        start_time = utc_now()
        conn.soc = conn.soc or random.uniform(18, 45)
        conn.txn = {"id": None, "idTag": id_tag, "meterStart": meter_start, "startTime": iso(start_time),
                    "start_ts": start_time.timestamp(), "energy_wh": 0.0, "samples": [],
                    "next_periodic": None, "next_clock": None, "source": auth.get("source", "rfid"),
                    "payment": auth.get("payment")}
        conn.message = "Starting..."
        payload = {"connectorId": conn.id, "idTag": id_tag, "meterStart": meter_start, "timestamp": iso(start_time)}
        if conn.reservation:
            payload["reservationId"] = conn.reservation["id"]
            conn.reservation = None
        result = await self.send_call("StartTransaction", payload, queue_if_offline=True)
        if result:
            self._apply_start_conf(conn.id, result)
        self.schedule_samples(conn)
        await self.update_status(conn)
        if conn.txn:
            await self.send_meter_values([conn], "Transaction.Begin")

    def _apply_start_conf(self, cid: Any, result: dict) -> None:
        conn = self.connectors.get(as_int(cid, 0))
        if conn is None or conn.txn is None:
            return
        conn.txn["id"] = as_int(result.get("transactionId"), 0)
        status = str((result.get("idTagInfo") or {}).get("status", "Accepted"))
        conn.message = f"Charging - txn #{conn.txn['id']}"
        if status != "Accepted":
            conn.message = f"Transaction refused ({status})"
            if as_bool(self.cfg_get("StopTransactionOnInvalidId", "true")):
                asyncio.create_task(self.stop_transaction(conn, "DeAuthorized"))

    async def stop_transaction(self, conn: Connector, reason: str) -> None:
        txn = conn.txn
        if not txn or txn.get("stopping"):
            return
        txn["stopping"] = True
        txn["finishing"] = True
        conn.power_w = conn.current_a = conn.voltage_v = 0.0
        await self.update_status(conn)
        end_sample = self.sample(conn, "Transaction.End", self.measurands("StopTxnSampledData"))
        await self.send_call("MeterValues", {"connectorId": conn.id, "transactionId": txn["id"] or 0,
                                             "meterValue": [end_sample]}, queue_if_offline=True)
        transaction_data = (txn["samples"][-100:] + [end_sample])
        payload = {"transactionId": txn["id"] or 0, "idTag": txn["idTag"], "meterStop": int(conn.meter_wh),
                   "timestamp": iso(), "reason": reason, "transactionData": transaction_data}
        await self.send_call("StopTransaction", payload, queue_if_offline=True)
        if txn.get("payment"):
            await self.payment_settled(conn, txn, reason)
        conn.txn = None
        for _pid in [k for k, p in conn.profiles.items() if p.get("chargingProfilePurpose") == "TxProfile"]:
            del conn.profiles[_pid]                       # [VC-LM] a TxProfile ends with its transaction
        conn.finished = conn.plugged
        conn.message = f"Stopped ({reason}) - {txn['energy_wh'] / 1000:.3f} kWh"
        if conn.pending_inoperative:
            conn.operative = False
            conn.pending_inoperative = False
        await self.update_status(conn)
        if conn.auto_plugged and conn.plugged:
            asyncio.create_task(self.simulated_unplug(conn))

    # ------------------------------------------------------------ meter values
    def measurands(self, key: str) -> list[str]:
        return [m.strip() for m in self.cfg_get(key, "Energy.Active.Import.Register").split(",") if m.strip()]

    def sample(self, conn: Connector, context: str, measurands: list[str], ts: Optional[float] = None) -> dict:
        values = []
        for m in measurands:
            if m == "Energy.Active.Import.Register":
                values.append({"value": str(int(conn.meter_wh)), "context": context, "measurand": m, "unit": "Wh", "location": "Outlet"})
            elif m == "Power.Active.Import":
                values.append({"value": str(int(conn.power_w)), "context": context, "measurand": m, "unit": "W", "location": "Outlet"})
            elif m == "Current.Import":
                values.append({"value": f"{conn.current_a:.1f}", "context": context, "measurand": m, "unit": "A", "location": "Outlet"})
            elif m == "Voltage":
                values.append({"value": f"{conn.voltage_v:.1f}", "context": context, "measurand": m, "unit": "V", "location": "Outlet"})
            elif m == "SoC":
                values.append({"value": f"{conn.soc:.0f}", "context": context, "measurand": m, "unit": "Percent", "location": "EV"})
            elif m == "Temperature":
                values.append({"value": f"{conn.temperature_c:.1f}", "context": context, "measurand": m, "unit": "Celsius", "location": "Body"})
        if not values:
            values.append({"value": str(int(conn.meter_wh)), "context": context, "measurand": "Energy.Active.Import.Register", "unit": "Wh"})
        stamp = iso(datetime.fromtimestamp(ts, timezone.utc)) if ts else iso()
        return {"timestamp": stamp, "sampledValue": values}

    async def send_meter_values(self, conns: list[Connector], context: str, ts: Optional[float] = None) -> None:
        key = "MeterValuesAlignedData" if context == "Sample.Clock" else "MeterValuesSampledData"
        for conn in conns:
            sample = self.sample(conn, context, self.measurands(key), ts)
            payload: dict = {"connectorId": conn.id, "meterValue": [sample]}
            if conn.txn and conn.txn.get("id"):
                payload["transactionId"] = conn.txn["id"]
                if context in ("Sample.Periodic", "Sample.Clock"):
                    conn.txn["samples"].append(sample)
            await self.send_call("MeterValues", payload, queue_if_offline=bool(conn.txn))

    def schedule_samples(self, conn: Connector) -> None:
        txn = conn.txn
        if not txn:
            return
        now = time.time()
        periodic = self.cfg_int("MeterValueSampleInterval", 0)
        clock = self.cfg_int("ClockAlignedDataInterval", 0)
        txn["next_periodic"] = (txn["start_ts"] + periodic) if periodic > 0 else None
        txn["next_clock"] = (math.floor(now / clock) + 1) * clock if clock > 0 else None

    async def simulation_loop(self) -> None:
        """1-second tick: energy flow, SoC, timers (meter samples, auth timeout, reservation expiry)."""
        last = time.time()
        while True:
            await asyncio.sleep(1.0)
            now = time.time()
            dt = (now - last) * self.speed
            last = now
            for conn in self.connectors.values():
                changed = False
                # authorization timeout (ConnectionTimeOut)
                if conn.auth and not conn.plugged and now > conn.auth["expires"]:
                    conn.auth = None
                    conn.message = "Authorization expired (no cable)"
                    changed = True
                # reservation expiry
                if conn.reservation:
                    exp = parse_iso(conn.reservation["expiry"])
                    if exp and exp <= utc_now():
                        conn.reservation = None
                        conn.message = "Reservation expired"
                        changed = True
                # charging physics
                if conn.txn and not conn.txn.get("stopping"):
                    target = conn.power_limit_w()
                    if conn.soc >= 100 or conn.txn.get("suspended_ev"):
                        target = 0
                    elif conn.soc > 80:
                        target *= max(0.15, (100 - conn.soc) / 20)      # taper
                    conn.power_w += (target - conn.power_w) * min(1.0, 0.35 * dt)
                    conn.voltage_v = 380 + conn.soc * 0.7 if conn.power_w > 100 else 0.0
                    conn.current_a = conn.power_w / conn.voltage_v if conn.voltage_v else 0.0
                    wh = conn.power_w * dt / 3600.0
                    conn.meter_wh += wh
                    conn.txn["energy_wh"] += wh
                    conn.soc = min(100.0, conn.soc + wh / (self.battery_kwh * 1000) * 100)
                    conn.temperature_c = min(55.0, conn.temperature_c + 0.02 * dt * (conn.power_w / max(1, conn.max_power_w)))
                    if conn.auto_plugged and conn.soc >= 100.0:
                        conn.full_since = conn.full_since or now
                        if now - conn.full_since >= 8 and not conn.txn.get("leaving"):
                            conn.txn["leaving"] = True
                            conn.message = "Battery full - the simulated driver is unplugging ..."
                            asyncio.create_task(self.simulated_leave(conn))
                    else:
                        conn.full_since = None
                    # periodic / clock-aligned samples (independent of speed multiplier: real wall-clock)
                    periodic = self.cfg_int("MeterValueSampleInterval", 0)
                    if periodic > 0:
                        if conn.txn["next_periodic"] is None:
                            # (re)started mid-transaction: next slot after now, still aligned to the transaction start
                            elapsed = max(0.0, now - conn.txn["start_ts"])
                            conn.txn["next_periodic"] = conn.txn["start_ts"] + (math.floor(elapsed / periodic) + 1) * periodic
                        if now >= conn.txn["next_periodic"]:
                            due = conn.txn["next_periodic"]
                            conn.txn["next_periodic"] += periodic
                            asyncio.create_task(self.send_meter_values([conn], "Sample.Periodic", due))
                    else:
                        conn.txn["next_periodic"] = None
                    clock = self.cfg_int("ClockAlignedDataInterval", 0)
                    if clock > 0:
                        if conn.txn["next_clock"] is None:
                            conn.txn["next_clock"] = (math.floor(now / clock) + 1) * clock
                        if now >= conn.txn["next_clock"]:
                            due = conn.txn["next_clock"]
                            conn.txn["next_clock"] += clock
                            asyncio.create_task(self.send_meter_values([conn], "Sample.Clock", due))
                    else:
                        conn.txn["next_clock"] = None
                    changed = True
                else:
                    conn.temperature_c = max(26.0, conn.temperature_c - 0.01 * dt)
                if changed:
                    await self.update_status(conn)

    # ------------------------------------------------------------ driver actions (front panel)
    async def action(self, name: str, p: dict) -> dict:
        conn = self.connectors.get(as_int(p.get("connector"), 1))
        async with self._lock:
            if name == "net_off":
                self.network_enabled = False
                self.last_error = "network cut by the operator"
                if self.ws is not None:
                    await self.ws.close(code=1001, reason="network off")
                self.log_event("SYS", "Network", "disabled by operator")
                return {"ok": True}
            if name == "net_on":
                self.network_enabled = True
                self.last_error = "reconnecting ..."
                self.retry_now()
                self.log_event("SYS", "Network", "enabled by operator")
                return {"ok": True}
            if name == "reboot":
                asyncio.create_task(self.reboot("PowerLoss"))
                return {"ok": True}
            if name == "reconnect":
                self.last_error = "reconnecting ..."
                self.registration = "Unknown"
                if self.ws is not None:
                    await self.ws.close(code=1001, reason="reconnect")
                self.retry_now()
                return {"ok": True}
            if name == "set_connection":
                raw_url = str(p.get("url", "")).strip()
                cp_id = clean_cp_id(p.get("id", self.cp_id)) or self.cp_id
                auto = is_auto(raw_url)
                url = "" if auto else normalize_csms_url(raw_url, cp_id)
                if not auto and not url:
                    return {"ok": False, "error": f"'{raw_url}' is not a usable CSMS address - type the IP (e.g. 192.168.1.10) or ws://<ip>:9000/ocpp, or leave it empty for automatic"}
                try:
                    speed = max(0.1, min(1000.0, float(p.get("speed", self.speed))))
                except (TypeError, ValueError):
                    speed = self.speed
                changed = auto != self.auto_url or (not auto and url != self.url) or cp_id != self.cp_id
                if "auto_plug" in p:
                    self.auto_plug = as_bool(p.get("auto_plug"))
                sec_changed = False                                                    # [VC-SEC]
                new_pw = str(p.get("password") or "").strip()
                if new_pw:
                    if new_pw == "-":
                        new_pw = ""
                    elif not (16 <= len(new_pw) <= 40) or any(ord(ch) < 33 or ord(ch) > 126 for ch in new_pw):
                        return {"ok": False, "error": "the charger password needs 16 to 40 characters (no spaces)"}
                    if new_pw != self.auth_key:
                        self.auth_key = new_pw
                        sec_changed = True
                if "security_profile" in p:
                    prof = max(0, min(2, as_int(p.get("security_profile"), self.security_profile)))
                    if prof >= 1 and not self.auth_key:
                        return {"ok": False, "error": "security profile 1 / 2 needs a charger password"}
                    if prof != self.security_profile:
                        self.security_profile, self.profile_fallback = prof, None
                        self.cfg["SecurityProfile"]["value"] = str(prof)
                        sec_changed = True
                elif not self.auth_key and self.security_profile:
                    self.security_profile = 0
                    self.cfg["SecurityProfile"]["value"] = "0"
                    sec_changed = True
                if sec_changed:
                    changed = True
                    if getattr(self.args, "fleet_member", False):
                        self.save_security()
                self.auto_url, self.speed, self.cp_id = auto, speed, cp_id
                if not auto:
                    self.url = url
                self.cfg.setdefault("ChargeBoxId", {"value": cp_id, "readonly": False})["value"] = cp_id
                self.cfg.setdefault("CentralSystemURL", {"value": "", "readonly": False})["value"] = AUTO if auto else url
                err = (save_charger_config(AUTO if auto else url, cp_id, speed, self.auto_plug, self.auth_key, self.security_profile)
                       if not getattr(self.args, "fleet_member", False)
                       else "demo fleet station - not saved")    # [VC-FLEET] charger_config.json is the single charger's
                self.log_event("SYS", "Settings", f"url={AUTO if auto else url} id={cp_id} speed=x{speed:g} auto_plug={self.auto_plug}" + (f" ({err})" if err else " (saved)"))
                if changed:
                    self._config_epoch += 1
                    self.last_error = "settings changed - reconnecting ..."
                    self.registration = "Unknown"
                    if self.ws is not None:
                        await self.ws.close(code=1001, reason="settings changed")
                    self.retry_now()
                return {"ok": True, "url": AUTO if auto else url, "id": cp_id, "speed": speed, "saved": err is None, "error": err}
            if conn is None:
                return {"ok": False, "error": "unknown connector"}
            if name == "plug":
                if conn.plugged:
                    return {"ok": False, "error": "already plugged"}
                conn.plugged = True
                conn.auto_plugged = False
                conn.finished = False
                conn.soc = random.uniform(18, 45)
                conn.message = "Cable connected"
                await self.update_status(conn)
                if conn.auth:
                    await self.try_start(conn)
                elif as_bool(self.cfg_get("AutoChargeEnabled", "false")) and as_bool(p.get("autocharge", False)):
                    mac = "".join(random.choice("0123456789ABCDEF") for _ in range(12))
                    tag = ("AUTO-" + mac)[:20]
                    conn.message = f"Autocharge: {tag}"
                    if await self.authorize(conn, tag, "autocharge"):
                        await self.try_start(conn)
                return {"ok": True}
            if name == "unplug":
                if conn.txn:
                    if as_bool(self.cfg_get("StopTransactionOnEVSideDisconnect", "true")):
                        await self.stop_transaction(conn, "EVDisconnected")
                    else:
                        conn.txn["suspended_ev"] = True
                conn.plugged = False
                conn.auto_plugged = False
                conn.auth = None
                conn.finished = False
                conn.message = "Cable removed"
                await self.update_status(conn)
                return {"ok": True}
            if name == "tap":
                tag = str(p.get("idTag", "")).strip()[:20]
                if not tag:
                    return {"ok": False, "error": "idTag required"}
                if conn.txn:
                    if conn.txn["idTag"].upper() == tag.upper():
                        await self.stop_transaction(conn, "Local")
                        return {"ok": True}
                    conn.message = "Different card - cannot stop this session"
                    return {"ok": False, "error": "different card"}
                if conn.error_code != "NoError" or not conn.operative:
                    conn.message = "Connector not available"
                    return {"ok": False, "error": "connector not available"}
                if await self.authorize(conn, tag, "rfid"):
                    await self.try_start(conn)
                    return {"ok": True}
                return {"ok": False, "error": conn.message}
            if name == "start_button":
                if not as_bool(self.cfg_get(f"EnableFrontButtons{conn.id}", "true")):
                    conn.message = "Start button disabled by CSMS"
                    return {"ok": False, "error": "front buttons disabled"}
                if conn.txn:
                    return {"ok": False, "error": "already charging"}
                if not conn.auth:
                    conn.message = "Tap a card or pay first"
                    return {"ok": False, "error": "not authorized"}
                if not conn.plugged:
                    conn.message = "Connect the cable first"
                    return {"ok": False, "error": "cable not connected"}
                await self.try_start(conn)
                return {"ok": True}
            if name == "stop_button":
                if not as_bool(self.cfg_get(f"EnableFrontButtons{conn.id}", "true")):
                    conn.message = "Stop button disabled by CSMS"
                    return {"ok": False, "error": "front buttons disabled"}
                if not conn.txn:
                    return {"ok": False, "error": "no transaction"}
                await self.stop_transaction(conn, "Local")
                return {"ok": True}
            if name == "pay":
                if conn.txn:
                    return {"ok": False, "error": "already charging"}
                amount = max(1, as_int(p.get("amount"), 200))
                tag = ("PAY-" + uuid.uuid4().hex.upper()[:16])[:20]
                # payment taken on the charger's own terminal (Bhutan QR shown on the charger display, simulated)
                info = {"connectorId": conn.id, "sessionId": uuid.uuid4().hex[:12], "state": "Paid", "idTag": tag,
                        "invoiceNo": f"INV{datetime.now().strftime('%y%m%d%H%M%S')}", "gateway": "BhutanQR-SIM",
                        "amount": amount, "paidAmount": amount, "paidRef": "MBOB" + uuid.uuid4().hex[:8].upper(),
                        "paidAt": iso(), "currency": PAY_CURRENCY, "tariff": PAY_TARIFF_PER_KWH, "tariffMode": "kWh",
                        "budgetKwh": round(amount / PAY_TARIFF_PER_KWH, 2)}
                await self.send_call("DataTransfer", {"vendorId": "Flexxfast", "messageId": "PaymentReceived",
                                                      "data": json.dumps(info, ensure_ascii=False)})
                conn.message = f"Paid Nu. {amount} at the charger - {tag}"
                if await self.authorize(conn, tag, "payment"):
                    conn.auth["payment"] = info
                    await self.try_start(conn)
                return {"ok": True, "idTag": tag}
            if name == "fault":
                code = str(p.get("code") or "OtherError")
                conn.error_code = code
                conn.vendor_error = str(p.get("vendorCode") or "SIM-FAULT")
                conn.message = f"FAULT {code}"
                if conn.txn:
                    await self.stop_transaction(conn, "EmergencyStop")
                await self.update_status(conn)
                return {"ok": True}
            if name == "clear_fault":
                conn.error_code, conn.vendor_error = "NoError", ""
                conn.message = "Fault cleared"
                await self.update_status(conn)
                return {"ok": True}
            if name == "suspend_ev":
                if conn.txn:
                    conn.txn["suspended_ev"] = not conn.txn.get("suspended_ev")
                    await self.update_status(conn)
                    return {"ok": True}
                return {"ok": False, "error": "no transaction"}
            return {"ok": False, "error": f"unknown action {name}"}

    async def payment_settled(self, conn: Connector, txn: dict, reason: str) -> None:
        info = dict(txn.get("payment") or {})
        used_kwh = round(txn["energy_wh"] / 1000, 3)
        used_amount = round(used_kwh * float(info.get("tariff", PAY_TARIFF_PER_KWH)), 2)
        paid = float(info.get("paidAmount", 0))
        info.update({"state": "Settled", "transactionId": txn["id"], "usedKwh": used_kwh,
                     "usedMinutes": round((time.time() - txn["start_ts"]) / 60, 1), "usedAmount": used_amount,
                     "remainingAmount": round(max(0.0, paid - used_amount), 2),
                     "refundStatus": "Pending" if paid - used_amount > 0.5 else "None",
                     "refundAmount": round(max(0.0, paid - used_amount), 2), "endReason": reason})
        await self.send_call("DataTransfer", {"vendorId": "Flexxfast", "messageId": "PaymentSettled",
                                              "data": json.dumps(info, ensure_ascii=False)})


def camel_to_snake(name: str) -> str:
    out = []
    for i, ch in enumerate(name):
        if ch.isupper() and i and (not name[i - 1].isupper() or (i + 1 < len(name) and name[i + 1].islower())):
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


# ---------------------------------------------------------------------------
# Front panel (web UI)
# ---------------------------------------------------------------------------
# BPC logo (PNG): panel header and browser-tab icon - the same artwork as the CSMS header
BPC_LOGO_B64 = (
    "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"
    "hw+P4vh/6+es57ynXVgAAAAASUVORK5CYII="
)

PANEL_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#0a7a3c"><title>Virtual Charger · BPC</title><link rel="icon" type="image/png" href="/favicon.ico">
<style>
:root{--bg-main:linear-gradient(135deg,#1b8a44 0%,#063d1f 100%);--card:rgba(255,255,255,.96);--text:#1e293b;--muted:#64748b;--line:#e2e8f0;
--accent:#1f9d49;--accent2:#0a7a3c;--accent-g:linear-gradient(135deg,#22a94f 0%,#0a7a3c 100%);--ok:#10b981;--ok-l:#d1fae5;--warn:#f59e0b;--warn-l:#fef3c7;
--bad:#ef4444;--bad-l:#fee2e2;--blue:#3b82f6;--blue-l:#dbeafe;--purple:#7c3aed;--purple-l:#ede9fe;
--shadow-sm:0 1px 2px 0 rgb(0 0 0/.05);--shadow:0 4px 6px -1px rgb(0 0 0/.1),0 2px 4px -2px rgb(0 0 0/.1);--shadow-xl:0 20px 25px -5px rgb(0 0 0/.12),0 8px 10px -6px rgb(0 0 0/.1)}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:var(--bg-main);color:var(--text);font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:14px}
header{display:flex;flex-wrap:wrap;gap:12px 20px;align-items:center;justify-content:space-between;padding:14px 28px;background:rgba(255,255,255,.1);
backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border-bottom:1px solid rgba(255,255,255,.2);box-shadow:0 4px 30px rgba(0,0,0,.1)}
.brand{display:flex;align-items:center;gap:14px;min-width:0}
.brand-logo{height:52px;width:auto;background:#fff;border-radius:12px;padding:3px;box-shadow:0 2px 8px rgba(0,0,0,.2);flex:none}
header h1{font-size:20px;font-weight:700;line-height:1.2;margin:0;color:#fff;display:flex;align-items:center;flex-wrap:wrap;gap:8px;text-shadow:0 2px 4px rgba(0,0,0,.1)}
.brand-sub{color:rgba(255,255,255,.85);font-size:12px;font-weight:600;letter-spacing:.3px;margin-top:3px}
.brand-sub2{color:rgba(255,255,255,.7);font-size:11.5px;margin-top:2px;word-break:break-all}
.hstatus{display:flex;flex-wrap:wrap;align-items:center;gap:8px;color:#fff;font-size:14px;font-weight:600;background:rgba(255,255,255,.2);padding:7px 8px 7px 16px;border-radius:50px}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;background:var(--bad);box-shadow:0 0 10px var(--bad);flex:none}
.dot.on{background:#4ade80;box-shadow:0 0 10px #4ade80;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.7;transform:scale(1.1)}}
.pill{display:inline-block;padding:3px 10px;border-radius:50px;font-size:11px;font-weight:800;letter-spacing:.3px;text-transform:uppercase;background:#e2e8f0;color:#475569;text-shadow:none;vertical-align:middle}
.pill.Accepted{background:var(--ok-l);color:#047857}.pill.Pending{background:var(--warn-l);color:#b45309}.pill.Rejected,.pill.Unknown{background:var(--bad-l);color:#b91c1c}
.top{padding:20px 28px 0;max-width:1500px;margin:0 auto}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:20px;padding:20px 28px 28px;max-width:1500px;margin:0 auto}
.card{background:var(--card);border-radius:16px;padding:0 18px 16px;box-shadow:var(--shadow-xl);border:1px solid rgba(255,255,255,.8);overflow:hidden;min-width:0}
.card h2{margin:0 -18px 14px;padding:12px 18px;font-size:15px;font-weight:700;color:var(--text);display:flex;justify-content:space-between;align-items:center;gap:8px;
background:linear-gradient(135deg,#f8fafc 0%,#e2e8f0 100%);border-bottom:1px solid var(--line)}
.card.conn{border-top:5px solid #94a3b8}.card.conn.Available{border-top-color:var(--ok)}.card.conn.Charging,.card.conn.Finishing{border-top-color:var(--blue)}
.card.conn.Preparing,.card.conn.SuspendedEV,.card.conn.SuspendedEVSE{border-top-color:var(--warn)}.card.conn.Reserved{border-top-color:var(--purple)}.card.conn.Faulted{border-top-color:var(--bad)}
.status{padding:3px 10px;border-radius:50px;font-weight:800;font-size:11px;letter-spacing:.3px;text-transform:uppercase;background:#e2e8f0;color:#475569}
.status.Available{background:var(--ok-l);color:#047857}.status.Charging,.status.Finishing{background:var(--blue-l);color:#1d4ed8}
.status.Preparing,.status.SuspendedEV,.status.SuspendedEVSE{background:var(--warn-l);color:#b45309}.status.Reserved{background:var(--purple-l);color:#6d28d9}.status.Faulted{background:var(--bad-l);color:#b91c1c}
.screen{background:linear-gradient(160deg,#0b3a22 0%,#052414 100%);border:1px solid #0f5132;border-radius:12px;padding:14px 16px;font-family:ui-monospace,'SF Mono',Consolas,monospace;
font-size:12px;line-height:1.65;min-height:120px;margin:0 0 10px;color:#d1fae5;box-shadow:inset 0 2px 10px rgba(0,0,0,.35)}
.screen .big{font-size:28px;font-weight:700;color:#4ade80;letter-spacing:.5px;text-shadow:0 0 12px rgba(74,222,128,.35)}.screen .msg{color:#fde68a}
.bar{height:8px;background:rgba(255,255,255,.14);border-radius:4px;overflow:hidden;margin:6px 0}.bar i{display:block;height:100%;background:linear-gradient(90deg,#22a94f,#86efac)}
.row{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px;align-items:center}
button{border:1px solid var(--line);border-radius:12px;padding:8px 14px;font:inherit;font-size:13px;font-weight:600;color:var(--text);cursor:pointer;
background:linear-gradient(135deg,#f8fafc 0%,#e2e8f0 100%);box-shadow:var(--shadow-sm);transition:all .2s cubic-bezier(.4,0,.2,1)}
button:hover:not(:disabled){background:var(--accent-g);color:#fff;border-color:transparent;transform:translateY(-1px);box-shadow:var(--shadow)}
button:active:not(:disabled){transform:translateY(0)}button:disabled{opacity:.4;cursor:not-allowed}
.b-ok{--btn:linear-gradient(135deg,#22c55e 0%,#16a34a 100%)}.b-bad{--btn:linear-gradient(135deg,#ef4444 0%,#dc2626 100%)}.b-warn{--btn:linear-gradient(135deg,#f59e0b 0%,#d97706 100%)}
.b-blue{--btn:linear-gradient(135deg,#3b82f6 0%,#2563eb 100%)}.b-purple{--btn:linear-gradient(135deg,#8b5cf6 0%,#7c3aed 100%)}.b-primary{--btn:var(--accent-g)}
:is(.b-ok,.b-bad,.b-warn,.b-blue,.b-purple,.b-primary),:is(.b-ok,.b-bad,.b-warn,.b-blue,.b-purple,.b-primary):hover:not(:disabled){background:var(--btn);color:#fff;border-color:transparent}
:is(.b-ok,.b-bad,.b-warn,.b-blue,.b-purple,.b-primary):hover:not(:disabled){filter:brightness(1.08)}
.hb-btn{padding:6px 13px;background:rgba(255,255,255,.22);color:#fff;border:1px solid rgba(255,255,255,.35);border-radius:8px;font-size:12.5px;font-weight:700;box-shadow:none}
.hb-btn:hover:not(:disabled){background:#fff;color:var(--accent2);border-color:#fff;transform:none}
.hb-btn.danger,.hb-btn.danger:hover:not(:disabled){background:rgba(239,68,68,.85);color:#fff;border-color:transparent}.hb-btn.danger:hover:not(:disabled){background:#ef4444}
input{border:1px solid var(--line);background:#fff;color:var(--text);border-radius:10px;padding:8px 11px;font:inherit;font-size:13px;width:130px}
input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgba(31,157,73,.12)}
input[type=checkbox]{width:16px;height:16px;padding:0;margin:0;accent-color:var(--accent)}
label.chk{color:var(--muted);font-size:12px;display:inline-flex;align-items:center;gap:6px}
table{width:100%;border-collapse:collapse;font-size:12px}td,th{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
.kv td:first-child{color:var(--muted);width:45%}.kv tr:last-child td{border-bottom:0}
.log{max-height:440px;overflow:auto;font-family:ui-monospace,'SF Mono',Consolas,monospace;font-size:11.5px;border:1px solid var(--line);border-radius:12px;background:#f8fafc}
.log td{border-bottom:1px solid #edf2f7;white-space:nowrap}.log .IN{color:#047857}.log .OUT{color:#c2410c}.log .SYS{color:#64748b}.log .ERR,.log .DROP{color:#b91c1c}.log .QUEUE{color:#b45309}
.log td:last-child{white-space:normal;word-break:break-all;color:#334155}.wide{grid-column:1/-1}
.toast{position:fixed;right:24px;bottom:24px;z-index:2000;background:#fff;color:var(--text);border-radius:16px;padding:14px 18px;min-width:260px;max-width:380px;font-weight:600;
box-shadow:var(--shadow-xl);border:1px solid var(--line);border-left:4px solid var(--ok);animation:toastIn .4s cubic-bezier(.4,0,.2,1)}
.toast.ok{background:linear-gradient(135deg,rgba(16,185,129,.07) 0%,#fff 100%)}.toast.bad{border-left-color:var(--bad);background:linear-gradient(135deg,rgba(239,68,68,.07) 0%,#fff 100%)}
@keyframes toastIn{from{transform:translateX(100%) scale(.9);opacity:0}to{transform:none;opacity:1}}
.hint{color:var(--muted);font-size:12px;margin-top:12px;line-height:1.55}.hint code,.card code{background:#f1f5f9;padding:1px 5px;border-radius:5px;font-family:ui-monospace,Consolas,monospace;font-size:11.5px}
.secret{-webkit-text-security:disc}.sub{color:var(--muted);font-size:12px}.sm{padding:5px 10px;font-size:12px;border-radius:10px}.err{color:#b45309;font-weight:600}.okt{color:#047857;font-weight:600}
label.f{display:inline-flex;flex-direction:column;gap:4px;color:var(--muted);font-size:11px;font-weight:700}label.f input{width:auto}
.payrow{align-items:center;gap:12px;background:#f0fdf4;border:1px dashed #86efac;border-radius:12px;padding:8px 10px}
.payqr img{width:84px;height:84px;background:#fff;border-radius:8px;display:block;border:1px solid var(--line)}.payrow .sub{font-size:11.5px;line-height:1.5}.payrow a{color:var(--accent2);font-weight:600}
.note{color:var(--muted);font-size:12px;line-height:1.55;margin:12px 0 0}
@media (max-width:640px){header{padding:12px 14px}.brand-logo{height:42px}header h1{font-size:17px}.hstatus{border-radius:16px;padding:8px 10px}
.top{padding:14px 12px 0}main{padding:14px 12px 18px;gap:14px;grid-template-columns:1fr}.card{padding:0 14px 14px}.card h2{margin:0 -14px 12px;padding:11px 14px}
.toast{left:12px;right:12px;bottom:12px;max-width:none;min-width:0}
.log table,.log tbody{display:block}.log tr{display:grid;grid-template-columns:auto auto 1fr;gap:0 10px;padding:6px 10px;border-bottom:1px solid #edf2f7}
.log td{display:block;padding:0;border:0}.log td:last-child{grid-column:1/-1;margin-top:2px}}
</style></head><body>
<header><div class="brand"><img class="brand-logo" src="/logo.png" alt="BPC">
<div><h1>Virtual Charger <span id="cpId"></span><span id="reg" class="pill">…</span></h1>
<div class="brand-sub">Bhutan Power Corporation Limited · OCPP 1.6 charging station simulator</div>
<div class="brand-sub2"><span id="url"></span> · FW <span id="fw"></span> · heartbeat <span id="hb">—</span> (every <span id="hbi">?</span>s)</div></div></div>
<div class="hstatus"><span id="dot" class="dot"></span><span id="online">connecting</span>
<button class="hb-btn" onclick="act('net_off')">📵 Cut network</button><button class="hb-btn" onclick="act('net_on')">📶 Restore network</button>
<button class="hb-btn danger" onclick="if(confirm('Simulate power loss / reboot?'))act('reboot')">🔁 Power-cycle</button></div></header>
<div class="top"><section class="card" id="conn"><h2>🔗 Connection to the CSMS <span id="cmode" class="pill">…</span></h2>
<div id="cstatus" class="sub">…</div><div id="found" class="row"></div>
<div class="row"><label class="f">CSMS address (empty = automatic)<input id="cUrl" placeholder="auto - finds the CSMS by itself" style="width:290px"></label>
<label class="f">Charge point ID<input id="cId" style="width:170px"></label><label class="f">Speed ×<input id="cSpeed" type="number" min="0.1" step="1" style="width:70px"></label>
<label class="f">Simulated driver<label class="chk" title="After a Remote Start (app / pay page) the simulator connects the cable by itself and removes it when the session ends - a real charger waits for the driver"><input type="checkbox" id="cAutoPlug"> auto plug-in on remote start</label></label>
<label class="f">Security profile<select id="cProf" style="width:170px" title="OCPP security profile: 1 = the charger signs in with its password (HTTP Basic Auth), 2 = password over TLS (wss://)"><option value="0">0 - no password</option><option value="1">1 - password</option><option value="2">2 - password + TLS</option></select></label>
<label class="f">Charger password (Basic Auth)<input id="cPw" class="secret" autocomplete="off" data-lpignore="true" placeholder="not set" style="width:210px" title="16-40 characters = OCPP AuthorizationKey; type - to remove it"></label>
<button class="b-primary" onclick="applyConn()">💾 Apply &amp; reconnect</button><button onclick="act('reconnect')">🔄 Reconnect now</button><button onclick="setAuto()">✨ Back to automatic</button></div>
<div class="hint">Automatic = the simulator looks for the CSMS running on this PC (same folder / ports 9000-9009) or in the same LAN (UDP broadcast) and connects by itself - nothing to type.
To use a specific CSMS, type its IP (e.g. <b>192.168.1.10</b>) - <code>ws://…:9000/ocpp</code> is added for you - or the full <code>ws://ip:port/ocpp</code> - a CSMS in the cloud: <code>wss://name.onrender.com/ocpp</code>. Settings are saved in <code>charger_config.json</code> next to the program.</div></section></div>
<main id="main"></main>
<div id="toast" class="toast" style="display:none"></div>
<script>
'use strict';
let state=null;const $=id=>document.getElementById(id);const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function toast(m,ok){const t=$('toast');t.textContent=m;t.className='toast '+(ok?'ok':'bad');t.style.display='block';clearTimeout(t._h);t._h=setTimeout(()=>t.style.display='none',3500)}
async function post(name,params){const r=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.assign({action:name},params||{}))});return await r.json()}
async function act(name,params){try{const j=await post(name,params);if(!j.ok)toast(j.error||'failed',false);else toast(name+' ✓',true);refresh()}catch(e){toast(String(e),false)}}
const dirty=new Set();['cUrl','cId','cSpeed','cPw'].forEach(id=>$(id).addEventListener('input',()=>dirty.add(id)));$('cProf').addEventListener('change',()=>dirty.add('cProf'));
$('cAutoPlug').addEventListener('change',()=>{dirty.add('cAutoPlug');applyConn()});
$('cUrl').addEventListener('keydown',e=>{if(e.key==='Enter')applyConn()});$('cId').addEventListener('keydown',e=>{if(e.key==='Enter')applyConn()});
function fillConn(s){$('cmode').textContent=s.autoUrl?'automatic':'manual';$('cmode').className='pill '+(s.online?'Accepted':'Pending');
$('cstatus').innerHTML=s.online?`<span class="okt">🟢 connected to <b>${esc(s.url)}/${esc(s.cpId)}</b>${s.securityProfile?' · 🔒 security profile '+s.securityProfile:''}</span>`:`<span class="err">🔴 ${esc(s.networkEnabled?(s.lastError||'connecting ...'):'network cut (press Restore network)')}</span>`;
const f=s.discovered||[];$('found').innerHTML=f.length?'<span class="sub">CSMS found:</span> '+f.map(d=>`<button class="b-ok sm" title="Web UI: ${esc(d.web)}" onclick="useUrl(${esc(JSON.stringify(d.url))})">${esc(d.host||d.url)}${d.local?' (this PC)':''} · OCPP ${esc(d.version)} · ${esc(d.url)}</button>`).join(' '):'';
if(!dirty.has('cUrl'))$('cUrl').value=s.autoUrl?'':s.url;if(!dirty.has('cId'))$('cId').value=s.cpId;if(!dirty.has('cSpeed'))$('cSpeed').value=s.speed;if(!dirty.has('cAutoPlug'))$('cAutoPlug').checked=!!s.autoPlug;if(!dirty.has('cProf'))$('cProf').value=String(s.securityProfile||0);$('cPw').placeholder=s.passwordSet?'set - type to change (- removes it)':'not set'}
async function applyConn(){try{const j=await post('set_connection',{url:$('cUrl').value,id:$('cId').value,speed:$('cSpeed').value,auto_plug:$('cAutoPlug').checked,security_profile:$('cProf').value,password:$('cPw').value});if(j.ok){dirty.clear();$('cPw').value='';toast(j.saved?'settings applied and saved ✓':'applied, but not saved: '+(j.error||''),!!j.saved)}else toast(j.error||'failed',false);refresh()}catch(e){toast(String(e),false)}}
function useUrl(u){$('cUrl').value=u;dirty.add('cUrl');applyConn()}
function setAuto(){$('cUrl').value='';dirty.add('cUrl');applyConn()}
function tagOf(n){return ($('tag'+n)?$('tag'+n).value.trim():'')||'TEST001'}
function card(c,cfg){const fb=String(cfg['EnableFrontButtons'+c.id]??'true')==='true';const t=c.transaction;
return `<div class="card conn ${esc(c.status)}"><h2>🔌 Connector ${c.id} <span class="status ${esc(c.status)}">${esc(c.status)}</span></h2>
<div class="screen"><div class="big">${t?(t.energyWh/1000).toFixed(3)+' kWh':(c.plugged?'READY':'— — —')}</div>
<div>${c.plugged?'🔌 cable connected':'⭕ no cable'} · ${c.auth?'🪪 '+esc(c.auth.idTag)+' ('+esc(c.auth.source)+')':'no authorization'}</div>
<div>${t?'⚡ '+(c.powerW/1000).toFixed(1)+' kW · '+c.voltageV+' V · '+c.currentA+' A · txn #'+(t.pending?'…':t.id)+' ('+esc(t.idTag)+')':'power 0 kW'} · limit ${(c.powerLimitW/1000).toFixed(1)} kW</div>
<div>🔋 SoC ${c.soc}% <div class="bar"><i style="width:${c.soc}%"></i></div></div>
<div>meter ${(c.meterWh/1000).toFixed(3)} kWh · 🌡 ${c.temperatureC} °C${c.reservation?' · 📅 reserved #'+c.reservation.id+' for '+esc(c.reservation.idTag):''}${c.errorCode!=='NoError'?' · ⚠️ '+esc(c.errorCode):''}</div>
<div class="msg">${esc(c.message||'')}</div></div>
<div class="row"><button class="b-blue" onclick="act('plug',{connector:${c.id},autocharge:$('auto${c.id}').checked})" ${c.plugged?'disabled':''}>🔌 Plug in</button>
<button onclick="act('unplug',{connector:${c.id}})" ${c.plugged?'':'disabled'}>⭕ Unplug</button>
<label class="chk"><input type="checkbox" id="auto${c.id}"> Autocharge (AUTO-…)</label></div>
<div class="row"><input id="tag${c.id}" placeholder="RFID idTag" value="TEST001"><button class="b-purple" onclick="act('tap',{connector:${c.id},idTag:tagOf(${c.id})})">🪪 Tap card</button>
<button class="b-purple" onclick="act('pay',{connector:${c.id},amount:$('amt${c.id}').value})" title="Payment taken on the charger's own terminal (DataTransfer PaymentReceived)">📱 Pay at charger (QR)</button><input id="amt${c.id}" type="number" value="200" style="width:80px"> Nu.</div>
${state.payUrl?`<div class="row payrow"><a class="payqr" href="${esc(state.payUrl)}/${c.id}" target="_blank" title="Open the CSMS pay page for this connector"><img src="/pay_qr.svg?c=${c.id}" alt="QR"></a><span class="sub">💳 <b>Pay via CSMS</b> — the sticker QR of this connector: <a href="${esc(state.payUrl)}/${c.id}" target="_blank">${esc(state.payUrl)}/${c.id}</a><br>(scan with a phone or click: the customer page pays and the CSMS sends Remote Start)</span></div>`:''}
<div class="row"><button class="b-ok" onclick="act('start_button',{connector:${c.id}})" ${fb?'':'disabled'}>▶ Start (front button)</button>
<button class="b-bad" onclick="act('stop_button',{connector:${c.id}})" ${fb?'':'disabled'}>■ Stop (front button)</button>
<button onclick="act('suspend_ev',{connector:${c.id}})" ${t?'':'disabled'}>⏸ EV pause</button></div>
<div class="row"><button class="b-warn" onclick="act('fault',{connector:${c.id},code:'OverCurrentFailure'})">⚠️ Trip fault</button><button onclick="act('clear_fault',{connector:${c.id}})">✅ Clear fault</button>
<span class="sub" style="font-size:11px">${fb?'front buttons enabled':'🔒 front buttons disabled by CSMS (EnableFrontButtons'+c.id+')'}</span></div></div>`}
function render(){const s=state;$('cpId').textContent=s.cpId;document.title=s.cpId+' · Virtual Charger · BPC';$('reg').textContent=s.registration;$('reg').className='pill '+s.registration;$('dot').className='dot'+(s.online?' on':'');
$('online').textContent=s.online?'online':(s.networkEnabled?'offline (reconnecting)':'network cut');$('url').textContent=(s.autoUrl?'auto: ':'')+(s.url||'searching for a CSMS …');$('fw').textContent=s.firmware;$('hb').textContent=s.lastHeartbeat||'—';$('hbi').textContent=s.heartbeatInterval;
const tags={};s.connectors.forEach(c=>{tags[c.id]={tag:$('tag'+c.id)?.value,amt:$('amt'+c.id)?.value,auto:$('auto'+c.id)?.checked}});
let h=s.connectors.map(c=>card(c,s.config)).join('');
h+=`<div class="card"><h2>⚙️ Settings seen from the CSMS</h2><table class="kv">${Object.entries(s.config).map(([k,v])=>`<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join('')}
<tr><td>Diagnostics / firmware</td><td>${esc(s.diagStatus)} / ${esc(s.fwStatus)}</td></tr><tr><td>Local list</td><td>v${s.localList.version} (${s.localList.count} tags)</td></tr>
<tr><td>Auth cache</td><td>${esc(s.authCache.join(', ')||'—')}</td></tr><tr><td>Queued offline msgs</td><td>${s.queued}</td></tr><tr><td>Uptime</td><td>${s.uptime}s</td></tr></table>
<p class="note">Teaching flow: (1) accept this station in the CSMS (<b>Stations</b> or <b>Registration</b> tab) → (2) Plug in (or let the simulated driver do it after a Remote Start) → (3) Tap card (add the idTag in the CSMS <b>RFID Cards</b> tab first, or use Remote Start from the CSMS) → (4) watch MeterValues → (5) Stop from the CSMS, the front button, or by unplugging. Payments: <b>Pay via CSMS</b> (scan the connector QR → customer page → Remote Start) or <b>Pay at charger</b> (the charger reports PaymentReceived / PaymentSettled itself).</p></div>`;
h+=`<div class="card wide"><h2>📋 OCPP traffic (newest first)</h2><div class="log"><table>${s.events.slice().reverse().map(e=>`<tr class="${esc(e.kind)}"><td>${esc(e.time)}</td><td>${esc(e.kind)}</td><td>${esc(e.action)}</td><td>${esc(e.detail)}</td></tr>`).join('')}</table></div></div>`;
$('main').innerHTML=h;s.connectors.forEach(c=>{const t=tags[c.id]||{};if(t.tag!==undefined)$('tag'+c.id).value=t.tag;if(t.amt!==undefined)$('amt'+c.id).value=t.amt;if(t.auto!==undefined)$('auto'+c.id).checked=t.auto})}
async function refresh(){try{state=await (await fetch('/api/state',{cache:'no-store'})).json();fillConn(state);if(document.activeElement&&document.activeElement.tagName==='INPUT')return;render()}catch(e){}}
refresh();setInterval(refresh,1000);
</script></body></html>"""


PAY_QR_PLACEHOLDER = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 84 84" width="84" height="84">'
                      '<rect width="84" height="84" fill="#fff"/><text x="42" y="40" font-size="9" text-anchor="middle" '
                      'fill="#777" font-family="sans-serif">QR not</text><text x="42" y="52" font-size="9" text-anchor="middle" '
                      'fill="#777" font-family="sans-serif">available</text></svg>')


async def start_panel(charger: VirtualCharger, host: str, first_port: int) -> int:
    app = web.Application()

    async def index(_req):
        return web.Response(text=PANEL_HTML, content_type="text/html")

    async def state(_req):
        return web.json_response(charger.state())

    async def action(req):
        try:
            body = await req.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)
        name = str(body.pop("action", ""))
        result = await charger.action(name, body)
        return web.json_response(result)

    logo_png = base64.b64decode(BPC_LOGO_B64)

    async def logo(_req):
        return web.Response(body=logo_png, content_type="image/png", headers={"Cache-Control": "max-age=86400"})

    qr_cache: dict = {}

    async def pay_qr(req):
        """The CSMS pay-QR of one connector, fetched server-side (browsers block cross-origin images in some setups)."""
        conn = str(req.query.get("c", "1"))
        base = charger.pay_page_url().split("/pay/")[0] if charger.pay_page_url() else ""
        if not conn.isdigit() or not base:
            return web.Response(text=PAY_QR_PLACEHOLDER, content_type="image/svg+xml")
        url = f"{base}/api/pay/qr.svg?cp={charger.cp_id}&c={conn}&scale=3"
        hit = qr_cache.get(url)
        if hit and time.time() - hit[0] < 120:
            return web.Response(body=hit[1], content_type="image/svg+xml")
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.get(url) as resp:
                    body = await resp.read()
                    if resp.status == 200 and body.startswith(b"<svg"):
                        qr_cache[url] = (time.time(), body)
                        return web.Response(body=body, content_type="image/svg+xml")
        except Exception:  # noqa: BLE001
            pass
        return web.Response(text=PAY_QR_PLACEHOLDER, content_type="image/svg+xml")

    app.router.add_get("/", index)
    app.router.add_get("/api/state", state)
    app.router.add_post("/api/action", action)
    app.router.add_get("/pay_qr.svg", pay_qr)
    app.router.add_get("/logo.png", logo)
    app.router.add_get("/favicon.ico", logo)
    runner = web.AppRunner(app, access_log=None)     # keep the console for OCPP traffic only
    await runner.setup()
    for port in range(first_port, first_port + 20):
        site = web.TCPSite(runner, host, port)
        try:
            await site.start()
            return port
        except OSError:
            continue
    raise RuntimeError("no free port for the panel")


# [VC-FLEET] demo fleet: several simulated BPC stations in one program ------------------------------------------------
FLEET_TOWNS = ["THIMPHU", "PARO", "PUNAKHA", "WANGDUE", "PHUENTSHOLING", "GELEPHU", "BUMTHANG", "TRONGSA", "MONGAR",
               "TRASHIGANG", "SAMDRUPJONGKHAR", "HAA", "DAGANA", "TSIRANG", "SARPANG", "ZHEMGANG", "LHUENTSE",
               "PEMAGATSHEL", "TRASHIYANGTSE", "GASA"]
FLEET_MAX = 50

FLEET_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#0a7a3c"><title>Demo fleet · Virtual Charger · BPC</title><link rel="icon" type="image/png" href="/favicon.ico">
<style>
:root{--text:#1e293b;--muted:#64748b;--line:#e2e8f0;--accent-g:linear-gradient(135deg,#22a94f 0%,#0a7a3c 100%);--ok-l:#d1fae5;--warn-l:#fef3c7;--bad-l:#fee2e2;--blue-l:#dbeafe;--purple-l:#ede9fe}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:linear-gradient(135deg,#1b8a44 0%,#063d1f 100%);color:var(--text);font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:14px}
header{display:flex;flex-wrap:wrap;gap:12px 20px;align-items:center;justify-content:space-between;padding:14px 28px;background:rgba(255,255,255,.1);
backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border-bottom:1px solid rgba(255,255,255,.2);box-shadow:0 4px 30px rgba(0,0,0,.1)}
.brand{display:flex;align-items:center;gap:14px;min-width:0}.brand img{height:52px;width:auto;background:#fff;border-radius:12px;padding:3px;box-shadow:0 2px 8px rgba(0,0,0,.2)}
h1{font-size:20px;line-height:1.2;margin:0;color:#fff;text-shadow:0 2px 4px rgba(0,0,0,.1)}.sub{color:rgba(255,255,255,.85);font-size:12px;font-weight:600;letter-spacing:.3px;margin-top:3px}
.sum{color:#fff;font-weight:600;background:rgba(255,255,255,.2);padding:8px 16px;border-radius:50px;display:flex;gap:8px;align-items:center}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;background:#ef4444;box-shadow:0 0 8px #ef4444;vertical-align:middle}
.dot.on{background:#4ade80;box-shadow:0 0 10px #4ade80}
main{max-width:1200px;margin:0 auto;padding:20px 28px}
.card{background:rgba(255,255,255,.96);border-radius:16px;box-shadow:0 20px 25px -5px rgb(0 0 0/.12);border:1px solid rgba(255,255,255,.8);overflow:hidden}
.card h2{margin:0;padding:12px 18px;font-size:15px;font-weight:700;background:linear-gradient(135deg,#f8fafc 0%,#e2e8f0 100%);border-bottom:1px solid var(--line)}
.wrap{overflow-x:auto}table{width:100%;border-collapse:collapse}th,td{padding:9px 14px;border-bottom:1px solid var(--line);text-align:left;font-size:13px;vertical-align:middle}
tr:last-child td{border-bottom:0}th{color:var(--muted);font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.4px}
.pill,.chip{display:inline-block;padding:3px 10px;border-radius:50px;font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.3px;background:#e2e8f0;color:#475569;margin:1px 2px;white-space:nowrap}
.Accepted,.Available{background:var(--ok-l);color:#047857}.Pending,.Preparing,.SuspendedEV,.SuspendedEVSE{background:var(--warn-l);color:#b45309}
.Rejected,.Faulted{background:var(--bad-l);color:#b91c1c}.Charging,.Finishing{background:var(--blue-l);color:#1d4ed8}.Reserved{background:var(--purple-l);color:#6d28d9}
a.btn{display:inline-block;padding:6px 12px;border-radius:10px;border:1px solid var(--line);background:linear-gradient(135deg,#f8fafc 0%,#e2e8f0 100%);color:var(--text);
text-decoration:none;font-weight:600;font-size:12px;white-space:nowrap}a.btn:hover{background:var(--accent-g);color:#fff;border-color:transparent}
.sid{white-space:nowrap}.err{color:#b45309;font-size:11px;margin-top:2px}.csms{color:var(--muted);font-size:12px;word-break:break-all}
.note{color:#fff;opacity:.92;font-size:12.5px;line-height:1.6;margin:14px 4px}
@media (max-width:700px){header{padding:12px 14px}.brand img{height:42px}h1{font-size:17px}main{padding:12px}.csms{display:none}}
</style></head><body>
<header><div class="brand"><img src="/logo.png" alt="BPC"><div><h1>Virtual Charger · Demo fleet</h1>
<div class="sub">Bhutan Power Corporation Limited · <span id="n">…</span> simulated OCPP 1.6 stations</div></div></div>
<div class="sum"><span id="dot" class="dot"></span><span id="online">connecting</span></div></header>
<main><div class="card"><h2>Stations</h2><div class="wrap"><table><thead><tr><th>Station</th><th>Connection</th><th>Registration</th><th>Connectors</th>
<th class="csms">CSMS</th><th></th></tr></thead><tbody id="rows"></tbody></table></div></div>
<p class="note">The fleet connects by itself to the <b>Flexxfast CSMS on this PC</b> (start it first). In the CSMS: <b>Stations</b> tab →
<b>Accept all pending</b>. The stations appear on the <b>Map</b> at their towns. Each station has its own front panel (plug in, tap card,
pay QR, faults). Close the Virtual Charger window to stop the fleet.</p></main>
<script>
'use strict';
const $=id=>document.getElementById(id);const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function row(s){const panel=location.protocol+'//'+location.hostname+':'+s.port+'/';
return '<tr><td class="sid"><b>'+esc(s.id)+'</b></td><td><span class="dot'+(s.online?' on':'')+'"></span> '+(s.online?'online':'offline')+
(s.online||!s.lastError?'':'<div class="err">'+esc(s.lastError)+'</div>')+'</td><td><span class="pill '+esc(s.registration)+'">'+esc(s.registration)+'</span></td><td>'+
(s.connectors||[]).map(c=>'<span class="chip '+esc(c.status)+'">C'+esc(c.id)+' '+esc(c.status)+'</span>').join('')+'</td><td class="csms">'+esc(s.url||'searching …')+
'</td><td><a class="btn" href="'+esc(panel)+'" target="_blank">Open panel</a></td></tr>'}
async function refresh(){try{const j=await (await fetch('/api/fleet',{cache:'no-store'})).json();const f=j.fleet||[];const on=f.filter(s=>s.online).length;
$('n').textContent=f.length;$('online').textContent=on+' / '+f.length+' online';$('dot').className='dot'+(on?' on':'');$('rows').innerHTML=f.map(row).join('')}catch(e){}}
refresh();setInterval(refresh,2000);
</script></body></html>"""


def fleet_ids(n: int) -> list:
    """BPC-<town> for the dzongkhag towns (the CSMS places simulated BPC-<town> stations on the map), then BPC-STATION-nn."""
    n = max(1, min(FLEET_MAX, int(n)))
    return [f"BPC-{FLEET_TOWNS[i]}" if i < len(FLEET_TOWNS) else f"BPC-STATION-{i + 1:02d}" for i in range(n)]


def fleet_running(port: int) -> bool:
    """A demo fleet already serves its overview page on this port (second start of the shortcut)."""
    import urllib.request
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/api/fleet", timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8") or "null")
            return resp.status == 200 and isinstance(data, dict) and "fleet" in data
    except Exception:  # noqa: BLE001
        return False


async def start_fleet_index(chargers: list, ports: list, host: str, first_port: int) -> int:
    """Overview page of the fleet: every station with its connection, registration, connectors and a panel link."""
    app = web.Application()
    logo_png = base64.b64decode(BPC_LOGO_B64)

    async def index(_req):
        return web.Response(text=FLEET_HTML, content_type="text/html")

    async def api(_req):
        rows = []
        for ch, port in zip(chargers, ports):
            st = ch.state()
            rows.append({"id": st["cpId"], "online": st["online"], "registration": st["registration"], "url": st["url"],
                         "auto": st["autoUrl"], "lastError": st["lastError"], "port": port,
                         "connectors": [{"id": c["id"], "status": c["status"]} for c in st["connectors"]]})
        return web.json_response({"fleet": rows, "count": len(rows)})

    async def logo(_req):
        return web.Response(body=logo_png, content_type="image/png", headers={"Cache-Control": "max-age=86400"})

    app.router.add_get("/", index)
    app.router.add_get("/api/fleet", api)
    app.router.add_get("/logo.png", logo)
    app.router.add_get("/favicon.ico", logo)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    for port in range(first_port, first_port + 40):
        if port in ports:
            continue
        try:
            await web.TCPSite(runner, host, port).start()
            return port
        except OSError:
            continue
    raise OSError("no free port for the demo fleet overview page")


async def _fleet_member(charger, delay: float) -> None:
    await asyncio.sleep(delay)                           # stagger the first connections a little
    await asyncio.gather(charger.connection_loop(), charger.simulation_loop())


async def run_fleet(args) -> None:
    ids = fleet_ids(args.fleet)
    base = args.panel_port if "panel_port" in getattr(args, "given", set()) else 8101
    index_first = max(1, base - 1)
    if fleet_running(index_first):
        url = f"http://localhost:{index_first}/"
        LOG.info("A demo fleet is already running: %s", url)
        if not args.no_browser:
            webbrowser.open(url)
        await asyncio.sleep(3)
        return
    chargers, ports = [], []
    next_port = base
    for cp_id in ids:
        member = argparse.Namespace(**vars(args))
        member.id = cp_id
        member.fleet_member = True
        charger = VirtualCharger(member)
        port = await start_panel(charger, args.panel_host, next_port)
        chargers.append(charger)
        ports.append(port)
        next_port = port + 1
    index_port = await start_fleet_index(chargers, ports, args.panel_host, index_first)
    LOG.info("=" * 64)
    LOG.info("  Demo fleet: %d simulated BPC stations (%s ... %s) -> %s", len(chargers), ids[0], ids[-1],
             chargers[0].url or "the Flexxfast CSMS on this PC (found automatically)")
    LOG.info("  Overview:   http://localhost:%s/   (front panels on ports %s-%s)", index_port, ports[0], ports[-1])
    LOG.info("  In the CSMS: Stations tab -> Accept all pending. Close this window to stop the fleet.")
    LOG.info("=" * 64)
    if not args.no_browser:
        asyncio.get_running_loop().call_later(1.0, lambda: webbrowser.open(f"http://localhost:{index_port}/"))
    await asyncio.gather(*(_fleet_member(ch, i * 0.3) for i, ch in enumerate(chargers)))


async def main(args) -> None:
    if getattr(args, "fleet", 0):                        # [VC-FLEET] --fleet N: N stations in this one program
        await run_fleet(args)
        return
    charger = VirtualCharger(args)
    port = await start_panel(charger, args.panel_host, args.panel_port)
    if not args.no_browser:
        # open the front panel in the default browser once the server is serving
        asyncio.get_running_loop().call_later(1.0, lambda: webbrowser.open(f"http://localhost:{port}/"))
    LOG.info("=" * 64)
    LOG.info("  Virtual charger %s -> %s", charger.cp_id,
             f"{charger.url}/{charger.cp_id}" if charger.url else "CSMS found automatically (same PC / same LAN)")
    LOG.info("  Front panel:  http://localhost:%s/", port)
    LOG.info("=" * 64)
    await asyncio.gather(charger.connection_loop(), charger.simulation_loop())


def apply_saved_config(args, skip: set) -> None:
    """Use what the operator saved from the panel page (charger_config.json next to the program), except for
    settings given on the command line. Nothing is asked in the console any more - without a saved address
    the simulator finds the CSMS by itself."""
    cfg_file = APP_DIR / CONFIG_FILE_NAME
    try:
        saved = json.loads(cfg_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    if "url" in saved and "url" not in skip:
        url = str(saved.get("url") or "")
        args.url = AUTO if is_auto(url) else (normalize_csms_url(url) or AUTO)
    if saved.get("id") and "id" not in skip:
        args.id = clean_cp_id(saved["id"]) or args.id
    if "speed" not in skip:
        try:
            args.speed = float(saved.get("speed", args.speed))
        except (TypeError, ValueError):
            pass
    if "auto_plug" not in skip and "auto_plug" in saved:
        args.auto_plug = as_bool(saved.get("auto_plug"))
    if "password" not in skip and saved.get("password"):                          # [VC-SEC]
        args.password = str(saved.get("password"))
    if "security_profile" not in skip and "security_profile" in saved:
        args.security_profile = as_int(saved.get("security_profile"), 0)
    if saved:
        print(f"Settings from {cfg_file.name}: CSMS={args.url}  id={args.id or default_cp_id()}  speed=x{args.speed:g}  "
              f"auto-plug={'on' if getattr(args, 'auto_plug', True) else 'off'}  (change them on the panel page)")
    else:
        print(f"No {cfg_file.name} yet: looking for the Flexxfast CSMS automatically as {args.id or default_cp_id()} (speed x{args.speed:g})")


def cli():
    ap = argparse.ArgumentParser(description="Flexxfast virtual charger (OCPP 1.6 simulator)")
    ap.add_argument("--url", default=os.environ.get("CSMS_URL", AUTO),
                    help="CSMS base URL without the CP id (ws://host:9000/ocpp), an IP address, or 'auto' = find it (default)")
    ap.add_argument("--id", default=os.environ.get("CP_ID", ""), help="charge point identity (default: SIM-<this PC's name>)")
    ap.add_argument("--connectors", type=int, default=2)
    ap.add_argument("--max-kw", type=float, default=60.0, help="max power per connector (kW)")
    ap.add_argument("--speed", type=float, default=20.0 if FROZEN else 1.0,
                    help="simulation speed multiplier for energy/SoC (1 = real time; the .exe defaults to 20 for demos)")
    ap.add_argument("--auto-plug", dest="auto_plug", default=True, type=lambda v: str(v).lower() not in ("0", "false", "no", "off"),
                    metavar="on|off", help="simulated driver connects the cable after a Remote Start and removes it after the "
                    "session (default on; a real charger waits for the driver)")
    ap.add_argument("--password", default=os.environ.get("CP_PASSWORD", ""),                    # [VC-SEC]
                    help="charger password = OCPP AuthorizationKey for HTTP Basic Auth (security profile 1 / 2)")
    ap.add_argument("--security-profile", dest="security_profile", type=int, default=None, choices=(0, 1, 2),
                    help="OCPP security profile (default: 1 when a password is given, else 0; 2 needs a wss:// CSMS)")
    ap.add_argument("--vendor", default="Flexxfast")
    ap.add_argument("--model", default="DC-Link-120")
    ap.add_argument("--panel-host", default="0.0.0.0")
    ap.add_argument("--panel-port", type=int, default=8082)
    ap.add_argument("--log", default="mock_cp.log")
    ap.add_argument("--no-prompt", action="store_true", help="(kept for compatibility - nothing is asked any more)")
    ap.add_argument("--no-browser", action="store_true", help="do not open the front panel in the browser automatically")
    ap.add_argument("--fleet", type=int, default=0, metavar="N",
                    help="demo fleet: N simulated BPC stations (BPC-THIMPHU, BPC-PARO, ...) in this one program, "
                         "connected to the Flexxfast CSMS on this PC (or --url); front panels on ports 8101+, "
                         "overview page on 8100")
    args = ap.parse_args()
    given = {a[2:].split("=")[0].replace("-", "_") for a in sys.argv[1:] if a.startswith("--")}
    args.given = given                                   # [VC-FLEET] which options were typed (fleet panel ports)
    if getattr(args, "fleet", 0) and "log" not in given:
        args.log = "mock_cp_fleet.log"                   # [VC-FLEET] the single Virtual Charger keeps mock_cp.log
    if not getattr(args, "fleet", 0):                    # [VC-FLEET] the fleet does not use the single charger's settings
        apply_saved_config(args, skip={k for k in ("url", "id", "speed", "auto_plug", "password", "security_profile") if k in given})
    if not args.id:
        args.id = default_cp_id()
    return args


if __name__ == "__main__":
    if FROZEN:
        os.chdir(APP_DIR)                                  # log + config next to the .exe
        for _stream in (sys.stdout, sys.stderr):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass
    args = cli()
    handlers = [logging.StreamHandler(sys.stdout)]
    if args.log:
        handlers.append(logging.FileHandler(args.log, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s", handlers=handlers)
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        pass
