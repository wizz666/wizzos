"""Larm vid fel – till mobilen via ntfy (och/eller en webhook, t.ex. Home Assistant).

Varje problem har en nyckel. Ett larm skickas när problemet uppstår, sedan är det
tyst tills det är löst – då kommer ett "löst"-meddelande. Så blir det inget spam.

Övervakas:
  - backup: misslyckad körning, tak nått, misslyckat återställningstest
  - appar: kraschar/startar om gång på gång eller har stannat utan att du stoppat dem
  - uppdateringar som misslyckades och backades automatiskt
  - disken (/DATA) nästan full
  - https-certifikatet går ut snart (förnyas annars automatiskt)
  - Docker svarar inte
"""
from __future__ import annotations

import glob
import json
import shutil
import threading
import time

import httpx

from . import config

STATE_FILE = config.HOME / "alerts.json"
DEFAULTS = {"enabled": False, "ntfy_url": "https://ntfy.sh", "ntfy_topic": "",
            "ntfy_token": "", "webhook_url": "", "disk_percent": 90}
_lock = threading.Lock()


def cfg() -> dict:
    return {**DEFAULTS, **(config.load().get("alerts") or {})}


def public_cfg() -> dict:
    c = cfg()
    out = {k: v for k, v in c.items() if k != "ntfy_token"}
    out["has_token"] = bool(c.get("ntfy_token"))
    out["active"] = [{"key": k, **v} for k, v in _state().get("active", {}).items()]
    out["history"] = _state().get("history", [])[-30:][::-1]
    return out


def save(changes: dict) -> dict:
    c = cfg()
    for k in ("ntfy_url", "ntfy_topic", "webhook_url"):
        if changes.get(k) is not None:
            c[k] = str(changes[k]).strip().rstrip("/") if k == "ntfy_url" else str(changes[k]).strip()
    if changes.get("ntfy_token"):
        c["ntfy_token"] = changes["ntfy_token"].strip()
    if changes.get("disk_percent") is not None:
        c["disk_percent"] = max(50, min(99, int(changes["disk_percent"])))
    if changes.get("enabled") is not None:
        c["enabled"] = bool(changes["enabled"])
    config.update(alerts=c)
    return public_cfg()


def _state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return {"active": {}, "history": []}


def _save_state(s: dict) -> None:
    s["history"] = s.get("history", [])[-100:]
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(STATE_FILE)


# ---------- skicka ----------

def _server_name() -> str:
    from . import system
    return config.load().get("server_name") or system.hostname()


def _click_url() -> str:
    from . import proxy
    return proxy.wizz_url() or ""


def send(title: str, message: str, priority: int = 4, tags: str = "warning", c: dict | None = None,
         actions: list | None = None, click: str = "") -> None:
    """Skicka direkt (utan dubblettskydd). Fel tas upp som exception.
    actions = ntfy-knappar i notisen (t.ex. Godkänn/Neka)."""
    from . import i18n
    lang = i18n.server_lang()
    title, message = i18n.tr(title, lang), i18n.tr(message, lang)
    c = c or cfg()
    sent = False
    errors = []
    title = f"WizzOS ({_server_name()}): {title}"
    if c.get("ntfy_url") and c.get("ntfy_topic"):
        body = {"topic": c["ntfy_topic"], "title": title, "message": message,
                "priority": priority, "tags": [t for t in tags.split(",") if t]}
        click = click or _click_url()
        if click:
            body["click"] = click
        if actions:
            body["actions"] = actions[:3]
        headers = {"Authorization": f"Bearer {c['ntfy_token']}"} if c.get("ntfy_token") else {}
        try:
            r = httpx.post(c["ntfy_url"], json=body, headers=headers, timeout=20)
            if r.status_code >= 400:
                raise RuntimeError(f"ntfy svarade {r.status_code}: {r.text[:120]}")
            sent = True
        except Exception as e:  # noqa: BLE001
            errors.append(f"ntfy: {e}")
    if c.get("webhook_url"):
        try:
            r = httpx.post(c["webhook_url"], json={"title": title, "message": message,
                                                   "priority": priority}, timeout=20)
            if r.status_code >= 400:
                raise RuntimeError(f"webhook svarade {r.status_code}")
            sent = True
        except Exception as e:  # noqa: BLE001
            errors.append(f"webhook: {e}")
    if not sent:
        raise RuntimeError("; ".join(errors) or "Ingen larmkanal är inställd.")


def raise_alert(key: str, title: str, message: str, priority: int = 4) -> None:
    """Problemet finns: larma första gången, sedan tyst tills det är löst."""
    with _lock:
        s = _state()
        if key in s.get("active", {}):
            s["active"][key]["last_seen"] = time.time()
            _save_state(s)
            return
        s.setdefault("active", {})[key] = {"title": title, "message": message,
                                           "since": time.time(), "last_seen": time.time()}
        s.setdefault("history", []).append({"time": time.time(), "kind": "problem",
                                            "title": title, "message": message})
        _save_state(s)
    c = cfg()
    if c.get("enabled"):
        try:
            send(title, message, priority, "rotating_light" if priority >= 5 else "warning", c)
        except Exception:  # noqa: BLE001
            pass


def resolve(key: str, message: str | None = None) -> None:
    """Problemet är borta: skicka "löst" om det tidigare larmats."""
    with _lock:
        s = _state()
        prev = s.get("active", {}).pop(key, None)
        if not prev:
            return
        msg = message or "Det fungerar igen."
        s.setdefault("history", []).append({"time": time.time(), "kind": "löst",
                                            "title": prev["title"], "message": msg})
        _save_state(s)
    c = cfg()
    if c.get("enabled"):
        try:
            send(f"Löst – {prev['title']}", msg, 3, "white_check_mark", c)
        except Exception:  # noqa: BLE001
            pass


def notify(title: str, message: str, priority: int = 3) -> None:
    """Engångsmeddelande (t.ex. en uppdatering som backades automatiskt)."""
    with _lock:
        s = _state()
        s.setdefault("history", []).append({"time": time.time(), "kind": "info",
                                            "title": title, "message": message})
        _save_state(s)
    c = cfg()
    if c.get("enabled"):
        try:
            send(title, message, priority, "information_source", c)
        except Exception:  # noqa: BLE001
            pass


# ---------- övervakning ----------

_bad_count: dict[str, int] = {}


def check_once() -> None:
    from . import apps, proxy
    c = cfg()
    ok, msg = apps.docker_ok()
    if not ok:
        raise_alert("docker", "Docker svarar inte", f"Inga appar kan styras just nu. ({msg[:200]})", 5)
        return
    resolve("docker")

    # Appar
    try:
        inst = apps.installed()["apps"]
    except Exception:  # noqa: BLE001
        inst = []
    names = set()
    for a in inst:
        n = a["name"]
        names.add(n)
        if a.get("user_stopped") or a.get("trial") or n in apps._busy:
            _bad_count.pop(n, None)
            resolve(f"app:{n}")
            continue
        bad = a["status"] in ("stopped", "restarting", "partial")
        _bad_count[n] = _bad_count.get(n, 0) + 1 if bad else 0
        if _bad_count[n] >= 3:  # ca 3 minuter i rad
            what = {"stopped": "har stannat", "restarting": "startar om gång på gång",
                    "partial": "går bara delvis"}[a["status"]]
            raise_alert(f"app:{n}", f"{a['title']} {what}",
                        f"{a['title']} {what}. Öppna WizzOS och tryck ✨ Förklara i loggen för hjälp.", 4)
        elif _bad_count[n] == 0:
            resolve(f"app:{n}", f"{a['title']} är igång igen.")
    for key in list(_state().get("active", {})):
        if key.startswith("app:") and key[4:] not in names:
            resolve(key, "Appen är borttagen.")

    # Disk
    try:
        du = shutil.disk_usage(config.DATA_ROOT)
        pct = du.used / du.total * 100
        limit = c.get("disk_percent", 90)
        if pct >= limit:
            raise_alert("disk", "Disken är nästan full",
                        f"/DATA är {pct:.0f} % full ({du.free / 1024 ** 3:.1f} GB ledigt). "
                        "Appar och backup kan sluta fungera när den är full.", 5)
        elif pct < limit - 5:
            resolve("disk", f"Disken har gott om plats igen ({pct:.0f} %).")
    except OSError:
        pass

    # Backup som uteblivit (t.ex. om servern var avstängd flera nätter)
    from . import backup
    bs = backup.summary()
    if bs["configured"] and bs.get("enabled"):
        lr = (bs.get("last_run") or {}).get("time")
        since = backup.cfg().get("configured_at") or 0
        ref = lr or since
        if ref and time.time() - ref > 36 * 3600:
            raise_alert("backup-old", "Ingen backup på över ett dygn",
                        "Den nattliga backupen har inte körts. Öppna Backup i WizzOS och tryck "
                        "\"Säkerhetskopiera nu\".", 4)
        elif lr:
            resolve("backup-old", "Backupen körs igen.")

    # https-certifikat
    px = proxy.settings()
    if px["enabled"]:
        crt = glob.glob(str(proxy.PROXY_DIR / "data" / "caddy" / "certificates" / "*"
                            / f"wildcard_.{px['domain']}" / "*.crt"))
        days = None
        if crt:
            try:
                import ssl
                info = ssl._ssl._test_decode_cert(crt[0])  # noqa: SLF001 – inbyggd, inga beroenden
                days = (ssl.cert_time_to_seconds(info["notAfter"]) - time.time()) / 86400
            except Exception:  # noqa: BLE001
                days = None
        if days is not None and days < 14:
            raise_alert("cert", "Https-certifikatet går snart ut",
                        f"Certifikatet för {px['domain']} går ut om {days:.0f} dagar och har inte förnyats. "
                        "Titta i proxyns logg under Inställningar → Egen adress.", 4)
        elif days is not None:
            resolve("cert", "Certifikatet är förnyat.")


_started = False


def start_monitor() -> None:
    global _started
    if _started:
        return
    _started = True

    def loop():
        time.sleep(90)  # låt appar starta efter en omstart
        while True:
            try:
                check_once()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(60)
    threading.Thread(target=loop, daemon=True).start()
