"""Home Assistant: WizzOS-sensorer i ditt hem.

Varje minut skickar WizzOS sitt läge till Home Assistant via dess REST-API
(POST /api/states/sensor.wizzos_*). Det kräver ingen YAML och inget tillägg –
bara adressen och en långlivad åtkomsttoken (Profil → Säkerhet i HA).

Sensorerna går att visa på instrumentpanelen och använda i automationer,
t.ex. "blinka hallampan röd när sensor.wizzos_status blir problem".
Obs: sensorer skapade så här finns tills HA startas om och kommer tillbaka
inom en minut därefter (WizzOS skickar dem igen).
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import httpx

from . import alerts, apps, backup, config, system

DEFAULTS = {"enabled": False, "url": "", "token": ""}
_status = {"last": 0, "ok": None, "error": "", "count": 0}
_lock = threading.Lock()


def cfg() -> dict:
    return {**DEFAULTS, **(config.load().get("homeassistant") or {})}


def public_cfg() -> dict:
    c = cfg()
    return {"enabled": c["enabled"], "url": c["url"], "has_token": bool(c["token"]), **_status}


def save(enabled: bool, url: str, token: str | None) -> dict:
    url = (url or "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        raise ValueError("Adressen ska börja med http:// eller https://")
    c = cfg()
    c.update(enabled=bool(enabled) and bool(url), url=url)
    if token and token.strip():
        c["token"] = token.strip()
    if not url:
        c["token"] = ""
    config.update(homeassistant=c)
    return public_cfg()


def _headers(c: dict) -> dict:
    return {"Authorization": f"Bearer {c['token']}", "Content-Type": "application/json"}


def test() -> dict:
    c = cfg()
    if not (c["url"] and c["token"]):
        raise ValueError("Fyll i adress och token först.")
    try:
        r = httpx.get(f"{c['url']}/api/", headers=_headers(c), timeout=10)
    except httpx.HTTPError as e:
        raise ValueError(f"Home Assistant svarar inte ({type(e).__name__}).")
    if r.status_code == 401:
        raise ValueError("Home Assistant godkände inte token.")
    if r.status_code != 200:
        raise ValueError(f"Home Assistant svarade {r.status_code}. Är adressen rätt?")
    n = push()
    return {"ok": True, "sensors": n}


def _iso(t: float | None) -> str | None:
    return datetime.fromtimestamp(t, timezone.utc).isoformat() if t else None


def sensors() -> dict[str, tuple]:
    """entity_id → (state, attributes)."""
    name = config.load().get("server_name") or system.hostname()
    st = system.stats()
    inst = [a for a in apps.installed()["apps"] if not a.get("trial")]
    running = [a for a in inst if a.get("status") == "running"]
    down = [a["title"] for a in inst if a.get("status") != "running" and not a.get("user_stopped")]
    probs = alerts.public_cfg().get("active", [])
    ok_docker, _ = apps.docker_ok()
    b = backup.summary()
    lr, lt = b.get("last_run"), b.get("last_test")

    if not ok_docker or down:
        status, text = "problem", ("Docker svarar inte." if not ok_docker else f"{', '.join(down)} har stannat.")
    elif probs:
        status, text = "varning", "; ".join(p.get("title", "") for p in probs)
    else:
        status, text = "ok", f"Alla {len(inst)} appar är igång. Allt är lugnt."

    if not b.get("configured"):
        bstate = "av"
    elif lr and not lr.get("ok"):
        bstate = "misslyckad"
    elif lt and lt.get("ok"):
        bstate = "bevisad"
    elif lr:
        bstate = "gjord"
    else:
        bstate = "väntar"

    out = {
        "sensor.wizzos_status": (status, {"friendly_name": f"{name} status", "icon": "mdi:server",
                                          "text": text, "problem": [p.get("title", "") for p in probs]}),
        "sensor.wizzos_appar_igang": (len(running), {"friendly_name": f"{name} appar igång", "icon": "mdi:apps",
                                                     "unit_of_measurement": "appar", "state_class": "measurement",
                                                     "totalt": len(inst),
                                                     "appar": {a["title"]: a.get("status") for a in inst}}),
        "sensor.wizzos_backup": (bstate, {"friendly_name": f"{name} backup", "icon": "mdi:shield-check",
                                          "mal": b.get("target", ""),
                                          "senaste": _iso(lr["time"]) if lr else None,
                                          "provaterstalld": _iso(lt["time"]) if lt else None}),
        "sensor.wizzos_senaste_backup": (_iso(lr["time"]) if lr else "unknown",
                                         {"friendly_name": f"{name} senaste backup", "device_class": "timestamp",
                                          "icon": "mdi:backup-restore"}),
        "sensor.wizzos_lagring": (round(st["disk"]["percent"], 1), {"friendly_name": f"{name} lagring", "icon": "mdi:harddisk",
                                                                    "unit_of_measurement": "%", "state_class": "measurement",
                                                                    "ledigt_gb": round((st["disk"]["total"] - st["disk"]["used"]) / 1e9, 1)}),
        "sensor.wizzos_processor": (round(st["cpu"], 1), {"friendly_name": f"{name} processor", "icon": "mdi:cpu-64-bit",
                                                          "unit_of_measurement": "%", "state_class": "measurement"}),
        "sensor.wizzos_minne": (round(st["mem"]["percent"], 1), {"friendly_name": f"{name} minne", "icon": "mdi:memory",
                                                                 "unit_of_measurement": "%", "state_class": "measurement"}),
    }
    try:
        from . import updater
        s = updater.state()
        last = (s.get("runs") or [{}])[-1]
        out["sensor.wizzos_uppdateringar"] = (len(s.get("available") or {}), {
            "friendly_name": f"{name} nya versioner", "icon": "mdi:update", "unit_of_measurement": "appar",
            "senaste_natt": _iso(last.get("time")),
            "uppdaterade": [u["title"] for u in last.get("updated", [])],
            "backade": [x["title"] for x in last.get("rolled_back", [])]})
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import nestward
        n = nestward.summary()
        if n.get("ok"):
            out["sensor.wizzos_hemmet"] = (n["score"], {"friendly_name": "Hemmets hälsa (Nestward)", "icon": "mdi:home-heart",
                                                        "unit_of_measurement": "poäng", "state_class": "measurement",
                                                        "betyg": n.get("grade_text", ""),
                                                        "att_gora": [i["text"] for i in n.get("items", [])]})
    except Exception:  # noqa: BLE001
        pass
    return out


def push() -> int:
    c = cfg()
    if not (c["url"] and c["token"]):
        return 0
    n = 0
    with httpx.Client(timeout=10, headers=_headers(c)) as cl:
        for eid, (state, attrs) in sensors().items():
            r = cl.post(f"{c['url']}/api/states/{eid}", json={"state": state, "attributes": attrs})
            if r.status_code == 401:
                raise RuntimeError("Home Assistant godkände inte token.")
            r.raise_for_status()
            n += 1
    with _lock:
        _status.update(last=int(time.time()), ok=True, error="", count=n)
    return n


_started = False


def start_pusher() -> None:
    global _started
    if _started:
        return
    _started = True

    def loop():
        time.sleep(45)
        while True:
            if cfg()["enabled"]:
                try:
                    push()
                except Exception as e:  # noqa: BLE001
                    with _lock:
                        _status.update(last=int(time.time()), ok=False, error=str(e)[:200])
            time.sleep(60)

    threading.Thread(target=loop, daemon=True, name="ha-push").start()
