"""Vad kostar min server? – i kronor, per app.

Varje minut mäts hur mycket processor varje app använder. Elförbrukningen
räknas fram med en enkel modell: grundförbrukning (servern är på) + en del som
växer med hur hårt den jobbar. Varje app får sin del av den rörliga delen.
Elpriset är antingen ett fast pris du anger eller hämtas live från Home
Assistant (t.ex. Tibber / Nord Pool), så kostnaden följer timpriset.

För en hyrd server (VPS) finns ingen elräkning – då fördelas månadskostnaden
efter hur mycket varje app använder.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime

import httpx
import psutil

from . import apps, config

STATE = config.HOME / "cost.json"
KEEP_DAYS = 35
PRESETS = {  # tomgång W, full last W
    "minipc": ("Mini-PC (NUC, N100 o.d.)", 8, 30),
    "pi": ("Raspberry Pi", 3, 7),
    "nas": ("NAS (2–4 diskar)", 20, 45),
    "pc": ("Vanlig dator / gammal PC", 35, 120),
    "server": ("Stor server", 70, 220),
    "custom": ("Egna värden", None, None),
    "vps": ("Hyrd server (VPS)", None, None),
}
DEFAULTS = {"kind": "minipc", "idle_w": 8, "max_w": 30, "price": 2.0, "ha_entity": "", "vps_month": 50.0}
_lock = threading.Lock()
_price_cache = {"t": 0, "v": None, "err": ""}
_last = {"apps": {}, "watts": 0.0, "time": 0}


def cfg() -> dict:
    saved = config.load().get("cost")
    c = {**DEFAULTS, **(saved or {})}
    if not saved:  # gissa: har datorn en publik adress är den troligen en hyrd server
        try:
            from . import security
            if security.public_ips():
                c["kind"] = "vps"
        except Exception:  # noqa: BLE001
            pass
    p = PRESETS.get(c["kind"])
    if p and p[1] is not None:
        c["idle_w"], c["max_w"] = p[1], p[2]
    return c


def save(ch: dict) -> dict:
    c = cfg()
    if ch.get("kind") in PRESETS:
        c["kind"] = ch["kind"]
    for k, lo, hi in (("idle_w", 0, 2000), ("max_w", 0, 4000), ("price", 0, 50), ("vps_month", 0, 100000)):
        if ch.get(k) is not None and ch.get(k) != "":
            c[k] = max(lo, min(hi, float(str(ch[k]).replace(",", "."))))
    if c["max_w"] < c["idle_w"]:
        c["max_w"] = c["idle_w"]
    if ch.get("ha_entity") is not None:
        e = str(ch["ha_entity"]).strip()
        if e and not e.startswith("sensor."):
            raise ValueError("Elpriset ska vara en sensor i Home Assistant, t.ex. sensor.electricity_price.")
        c["ha_entity"] = e
    config.update(cost=c)
    _price_cache["t"] = 0
    return c


# ---------- elpris ----------

def price() -> tuple[float, str]:
    """(kr per kWh, källa)."""
    c = cfg()
    if c["ha_entity"]:
        now = time.time()
        if now - _price_cache["t"] > 600:
            _price_cache["t"] = now
            try:
                from . import homeassistant
                h = homeassistant.cfg()
                r = httpx.get(f"{h['url']}/api/states/{c['ha_entity']}",
                              headers={"Authorization": f"Bearer {h['token']}"}, timeout=10)
                r.raise_for_status()
                j = r.json()
                v = float(j["state"])
                unit = (j.get("attributes", {}).get("unit_of_measurement") or "").lower()
                if "öre" in unit or "ore" in unit:
                    v /= 100
                _price_cache.update(v=v, err="")
            except Exception as e:  # noqa: BLE001
                _price_cache["err"] = f"Kunde inte läsa elpriset från Home Assistant ({type(e).__name__})."
        if _price_cache["v"] is not None:
            return _price_cache["v"], "live"
    return float(c["price"]), "fast"


# ---------- mätning ----------

def _sample() -> dict:
    """{app: cpu-andel 0..1 av hela datorn} + total."""
    cores = psutil.cpu_count() or 1
    names = apps.docker("ps", "--format", '{{.Names}}\t{{.Label "com.docker.compose.project"}}', timeout=20)
    proj = {}
    for line in names.splitlines():
        n, _, p = line.partition("\t")
        proj[n.strip()] = p.strip() or n.strip()
    stats = apps.docker("stats", "--no-stream", "--format", "{{.Name}}\t{{.CPUPerc}}", timeout=40)
    mine = {d.name for d in config.APPS_DIR.iterdir()} if config.APPS_DIR.exists() else set()
    out: dict[str, float] = {}
    for line in stats.splitlines():
        n, _, c = line.partition("\t")
        try:
            v = float(c.strip().rstrip("%")) / 100 / cores
        except ValueError:
            continue
        p = proj.get(n.strip(), n.strip())
        key = p if p in mine else ("_wizzos" if p == "wizzos" else "_other")
        out[key] = out.get(key, 0) + v
    return out


def _load() -> dict:
    try:
        return json.loads(STATE.read_text("utf-8"))
    except (OSError, ValueError):
        return {"days": {}}


def _save(s: dict) -> None:
    days = sorted(s["days"])
    for d in days[:-KEEP_DAYS]:
        s["days"].pop(d)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False), "utf-8")
    tmp.replace(STATE)


def tick(seconds: float) -> None:
    c = cfg()
    shares = _sample()
    host = min(1.0, psutil.cpu_percent(None) / 100)
    dyn_range = max(0.0, c["max_w"] - c["idle_w"])
    app_w = {k: dyn_range * v for k, v in shares.items()}
    total_w = c["idle_w"] + dyn_range * max(host, sum(shares.values()))
    known = sum(app_w.values())
    app_w["_other"] = app_w.get("_other", 0) + max(0.0, total_w - c["idle_w"] - known)
    kr_per_kwh, _ = price()
    h = seconds / 3600
    day = datetime.now().strftime("%Y-%m-%d")
    with _lock:
        s = _load()
        d = s["days"].setdefault(day, {"wh": {}, "kr": {}, "cpu": {}, "hours": 0.0})
        d["hours"] += h
        for k, w in list(app_w.items()) + [("_base", c["idle_w"])]:
            d["wh"][k] = d["wh"].get(k, 0) + w * h
            d["kr"][k] = d["kr"].get(k, 0) + w * h / 1000 * kr_per_kwh
        for k, v in shares.items():
            d["cpu"][k] = d["cpu"].get(k, 0) + v * h
        _save(s)
    _last.update(apps=app_w, watts=total_w, time=time.time())


# ---------- sammanställning ----------

def summary() -> dict:
    c = cfg()
    s = _load()
    days = sorted(s["days"])[-7:]
    hours = sum(s["days"][d]["hours"] for d in days)
    titles = {a["name"]: a["title"] for a in apps.installed()["apps"]}
    titles.update({"_base": "Grundförbrukning (att servern är på)", "_wizzos": "WizzOS själv",
                   "_other": "Övrigt (andra program på datorn)"})
    kr_now, src = price()
    rows = []
    if c["kind"] == "vps":
        cpu = {}
        for d in days:
            for k, v in s["days"][d]["cpu"].items():
                cpu[k] = cpu.get(k, 0) + v
        tot = sum(cpu.values()) or 1
        for k, v in cpu.items():
            rows.append({"key": k, "title": titles.get(k, k), "month_kr": c["vps_month"] * v / tot,
                         "share": v / tot, "watts": None})
        month = c["vps_month"]
    else:
        wh, kr = {}, {}
        for d in days:
            for k, v in s["days"][d]["wh"].items():
                wh[k] = wh.get(k, 0) + v
            for k, v in s["days"][d]["kr"].items():
                kr[k] = kr.get(k, 0) + v
        if hours >= 0.5:
            scale = 730 / hours  # timmar per månad
            for k in wh:
                rows.append({"key": k, "title": titles.get(k, k), "watts": wh[k] / hours,
                             "month_kr": kr[k] * scale, "share": 0})
        else:  # för lite mätt än – visa ögonblicksbild
            for k, w in list(_last["apps"].items()) + [("_base", c["idle_w"])]:
                rows.append({"key": k, "title": titles.get(k, k), "watts": w,
                             "month_kr": w * 730 / 1000 * kr_now, "share": 0})
        month = sum(r["month_kr"] for r in rows)
        for r in rows:
            r["share"] = r["month_kr"] / month if month else 0
    rows = [r for r in rows if r["month_kr"] >= 0.05 or r["key"] == "_base" or r["key"] in titles and not r["key"].startswith("_")]
    rows.sort(key=lambda r: (r["key"] == "_base", -r["month_kr"]))
    apps_only = [r for r in rows if not r["key"].startswith("_")]
    tip = ""
    if c["kind"] != "vps" and apps_only:
        top = max(apps_only, key=lambda r: r["month_kr"])
        if top["month_kr"] >= 5:
            tip = (f"{top['title']} kostar mest, ungefär {top['month_kr']:.0f} kr i månaden. "
                   "Stoppa den när du inte använder den så sparar du det mesta.")
        base = next((r for r in rows if r["key"] == "_base"), None)
        if base and month and base["month_kr"] / month > 0.6:
            tip = tip or ("Det mesta av kostnaden är att servern är på. Apparna själva drar nästan inget – "
                          "en snålare dator (t.ex. en mini-PC) är det som sparar mest.")
    return {"kind": c["kind"], "kind_title": PRESETS[c["kind"]][0], "month_kr": month,
            "year_kr": month * 12, "watts_now": _last["watts"], "price": kr_now, "price_source": src,
            "price_error": _price_cache["err"] if c["ha_entity"] else "", "measured_hours": round(hours, 1),
            "rows": rows, "tip": tip, "cfg": c,
            "presets": {k: {"title": v[0], "idle_w": v[1], "max_w": v[2]} for k, v in PRESETS.items()}}


_started = False


def start_meter() -> None:
    global _started
    if _started:
        return
    _started = True

    def loop():
        time.sleep(20)
        prev = time.time()
        while True:
            now = time.time()
            try:
                tick(min(now - prev, 180))
            except Exception:  # noqa: BLE001
                pass
            prev = now
            time.sleep(60)

    threading.Thread(target=loop, daemon=True, name="cost-meter").start()
