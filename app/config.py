"""Inställningar och sökvägar för WizzOS."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

# /DATA är samma sökväg på värden och i containern, så att sökvägar i
# appbutikens compose-filer (/DATA/AppData/...) blir rätt på värden.
DATA_ROOT = Path(os.environ.get("WIZZOS_DATA", "/DATA"))
HOME = DATA_ROOT / "WizzOS"
APPS_DIR = HOME / "apps"
STORES_DIR = HOME / "stores"
SETTINGS_FILE = HOME / "settings.json"
# Värdens filsystem monteras skrivskyddat här (för att se /dev och sökvägar
# utanför /DATA). Saknas den kör vi utan kontrollen.
HOST_ROOT = Path(os.environ.get("WIZZOS_HOST_ROOT", "/host"))

SUGGESTED_STORES = [
    {
        "id": "casaos",
        "name": "CasaOS officiella",
        "url": "https://github.com/IceWhaleTech/CasaOS-AppStore/archive/refs/heads/main.zip",
    },
    {
        "id": "linuxserver",
        "name": "LinuxServer.io",
        "url": "https://github.com/WisdomSky/LinuxServer-AppStore/archive/refs/heads/main.zip",
    },
    {
        "id": "play",
        "name": "CasaOS Play",
        "url": "https://github.com/Cp0204/CasaOS-AppStore-Play/archive/refs/heads/main.zip",
    },
    {
        "id": "coolstore",
        "name": "Coolstore",
        "url": "https://github.com/WisdomSky/CasaOS-Coolstore/archive/refs/heads/main.zip",
    },
]

DEFAULTS = {
    "server_name": "",
    "tz": os.environ.get("TZ", "Europe/Stockholm"),
    "puid": "1000",
    "pgid": "1000",
    # Tom = appar nås på alla nätverkskort. 127.0.0.1 eller en Tailscale-IP
    # gör att appar bara nås därifrån (viktigt på en VPS).
    "bind_ip": os.environ.get("WIZZOS_BIND_IP", ""),
    "stores": [SUGGESTED_STORES[0], SUGGESTED_STORES[1]],
    # Appdata större än så här sparas inte i återställningspunkter.
    "undo_max_gb": 20,
    "password_hash": "",
    "secret": "",
}

_lock = threading.Lock()


def ensure_dirs() -> None:
    for d in (HOME, APPS_DIR, STORES_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load() -> dict:
    with _lock:
        data = dict(DEFAULTS)
        if SETTINGS_FILE.exists():
            try:
                data.update(json.loads(SETTINGS_FILE.read_text("utf-8")))
            except (OSError, ValueError):
                pass
        return data


def save(data: dict) -> None:
    with _lock:
        ensure_dirs()
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(SETTINGS_FILE)


def update(**changes) -> dict:
    data = load()
    data.update(changes)
    save(data)
    return data


def public(data: dict | None = None) -> dict:
    data = data or load()
    return {k: v for k, v in data.items()
            if k not in ("password_hash", "secret", "cf_token", "backup", "ai", "alerts", "nestward", "homeassistant", "mail", "legacy", "paperless")}
