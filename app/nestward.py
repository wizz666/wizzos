"""Koppling till Nestward (vakttornet som ser alla maskiner i hemmet).

Två riktningar:
1. WizzOS skriver /DATA/WizzOS/status.json varje minut. En Nestward-agent på
   samma maskin läser den: apparna får sina riktiga namn, WizzOS-backupen
   räknas (och provåterställningen räknas som bevis), och appar på prov eller
   som du stoppat själv ger inga larm. Filen innehåller inga hemligheter.
2. WizzOS hämtar Nestwards hälsopoäng med en läsnyckel och visar den på
   startsidan. Nyckeln stannar på servern och kan bara läsa sammanfattningen.
"""
from __future__ import annotations

import json
import os
import threading
import time

import httpx

from . import __version__, config

STATUS_FILE = config.HOME / "status.json"
DEFAULTS = {"url": "", "token": ""}
_cache: dict = {"at": 0.0, "data": None}
_cache_lock = threading.Lock()


def cfg() -> dict:
    return {**DEFAULTS, **(config.load().get("nestward") or {})}


def public_cfg() -> dict:
    c = cfg()
    return {"url": c["url"], "has_token": bool(c["token"]), "status_file": str(STATUS_FILE)}


def save(url: str, token: str | None) -> dict:
    url = (url or "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        raise ValueError("Adressen ska börja med http:// eller https://")
    c = cfg()
    c["url"] = url
    if token is not None and token.strip():
        c["token"] = token.strip()
    if not url:
        c["token"] = ""
    config.update(nestward=c)
    with _cache_lock:
        _cache.update(at=0.0, data=None)
    return public_cfg()


def summary(force: bool = False) -> dict:
    """Nestwards hälsosammanfattning (sparas en minut)."""
    c = cfg()
    if not c["url"] or not c["token"]:
        return {"configured": False}
    with _cache_lock:
        if not force and _cache["data"] and time.time() - _cache["at"] < 60:
            return _cache["data"]
    try:
        r = httpx.get(f"{c['url']}/api/v1/share/summary?lang=sv", timeout=8,
                      headers={"Authorization": f"Bearer {c['token']}"})
    except httpx.HTTPError as e:
        out = {"configured": True, "ok": False, "error": f"Nestward svarar inte ({type(e).__name__})."}
    else:
        if r.status_code == 200:
            out = {"configured": True, "ok": True, **r.json()}
        elif r.status_code == 401:
            out = {"configured": True, "ok": False, "error": "Nestward godkände inte nyckeln."}
        elif r.status_code == 404:
            out = {"configured": True, "ok": False,
                   "error": "Nestward har ingen läsnyckel påslagen (eller är äldre än 0.10)."}
        else:
            out = {"configured": True, "ok": False, "error": f"Nestward svarade {r.status_code}."}
    out["url"] = c["url"]
    with _cache_lock:
        _cache.update(at=time.time(), data=out)
    return out


def write_status() -> None:
    from . import apps, backup
    inst = apps.installed()["apps"]
    st = backup.state()
    c = backup.public_cfg()
    last_test = None
    for name, a in (st.get("apps") or {}).items():
        if a.get("last_test") and (not last_test or a["last_test"] > last_test["time"]):
            last_test = {"time": int(a["last_test"]), "ok": bool(a.get("test_ok")), "app": name}
    lr = st.get("last_run")
    data = {
        "product": "WizzOS", "version": __version__, "updated": int(time.time()),
        "apps": [{"name": a["name"], "title": a.get("title", a["name"]), "status": a.get("status", ""),
                  "trial": bool(a.get("trial")), "user_stopped": bool(a.get("user_stopped"))} for a in inst],
        "backup": {
            "configured": bool(c.get("configured")),
            "target": c.get("target", ""),
            "last_run": ({"time": int(lr["time"]), "ok": bool(lr.get("ok")),
                          "error": ", ".join(lr.get("failed") or [])} if lr else None),
            "last_test": last_test,
        },
    }
    tmp = STATUS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    os.chmod(tmp, 0o644)
    tmp.replace(STATUS_FILE)


_started = False


def start_writer() -> None:
    global _started
    if _started:
        return
    _started = True

    def loop():
        time.sleep(20)
        while True:
            try:
                write_status()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(60)

    threading.Thread(target=loop, daemon=True, name="nestward-status").start()
