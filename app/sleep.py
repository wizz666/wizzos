"""Sovande appar: en app som ingen använt på ett tag stoppas och frigör minnet.

Den som öppnar appens adress (via WizzOS-proxyn) väcker den – Caddy får inget svar,
skickar besökaren till WizzOS väckningssida, appen startar och sidan laddas om.
Utan proxyn väcks appen när du öppnar den från WizzOS.

Aktivitet = nätverkstrafik in/ut för appens behållare. Appar som pratar mycket i
bakgrunden somnar därför aldrig – det är meningen.
Sovande appar markeras även user_stopped, så larm, agent, nattuppdatering m.m. lämnar dem i fred.
"""
from __future__ import annotations

import json
import re
import secrets
import threading
import time

from . import apps, config

CHOICES = (0, 30, 60, 180)        # minuter; 0 = av
IDLE_BYTES = 30_000                # mindre trafik än så per minut räknas som "ingen använder den"
_UNIT = {"b": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9, "tb": 1e12, "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3}

_last_bytes: dict[str, float] = {}
_last_active: dict[str, float] = {}
_waking: set[str] = set()
_lock = threading.Lock()


def wake_key() -> str:
    k = config.load().get("wake_key")
    if not k:
        k = secrets.token_urlsafe(24)
        config.update(wake_key=k)
    return k


def _meta_file(name: str):
    return apps.app_dir(name) / "meta.json"


def _read(name: str) -> dict:
    return json.loads(_meta_file(name).read_text("utf-8"))


def _write(name: str, **ch) -> None:
    m = _read(name)
    m.update(ch)
    _meta_file(name).write_text(json.dumps(m, ensure_ascii=False, indent=2), "utf-8")


def _size(s: str) -> float:
    m = re.match(r"\s*([\d.]+)\s*([a-zA-Z]+)", s or "")
    return float(m.group(1)) * _UNIT.get(m.group(2).lower(), 1) if m else 0.0


def _stats() -> dict[str, dict]:
    """Per compose-projekt: summa nätverkstrafik (byte) och minne (MB)."""
    proj = {}
    for line in apps.docker("ps", "--format", '{{.Names}}\t{{.Label "com.docker.compose.project"}}').splitlines():
        if "\t" in line:
            n, p = line.split("\t", 1)
            if p:
                proj[n] = p
    out: dict[str, dict] = {}
    raw = apps.docker("stats", "--no-stream", "--format", "{{.Name}}\t{{.NetIO}}\t{{.MemUsage}}", timeout=40)
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or parts[0] not in proj:
            continue
        rx, _, tx = parts[1].partition("/")
        o = out.setdefault(proj[parts[0]], {"bytes": 0.0, "mem_mb": 0.0})
        o["bytes"] += _size(rx) + _size(tx)
        o["mem_mb"] += _size(parts[2].split("/")[0]) / 1e6
    return out


def set_sleep(name: str, minutes: int) -> dict:
    if minutes not in CHOICES:
        raise ValueError("Välj av, 30, 60 eller 180 minuter.")
    m = _read(name)
    if m.get("trial"):
        raise ValueError("En app på prov kan inte sova.")
    _write(name, sleep_after=minutes)
    _last_active[name] = time.time()
    if not minutes and m.get("asleep"):
        wake(name)
    return {"ok": True, "sleep_after": minutes}


def put_to_sleep(name: str, mem_mb: float = 0.0) -> None:
    import subprocess
    r = subprocess.run(apps._compose(name, "stop"), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "Kunde inte söva appen.")
    _write(name, asleep=True, user_stopped=True, asleep_since=int(time.time()), sleep_mem_mb=round(mem_mb))


def wake(name: str) -> bool:
    """Starta en sovande app. True om den väcks (eller redan håller på)."""
    with _lock:
        if name in _waking:
            return True
        try:
            m = _read(name)
        except (OSError, ValueError):
            return False
        if not m.get("asleep"):
            return False
        _waking.add(name)

    def run():
        import subprocess
        try:
            subprocess.run(apps._compose(name, "start"), capture_output=True, timeout=180)
            _write(name, asleep=False, user_stopped=False, asleep_since=None)
            _last_active[name] = time.time()
            _last_bytes.pop(name, None)
        finally:
            _waking.discard(name)
    threading.Thread(target=run, daemon=True).start()
    return True


def app_for_host(host: str) -> dict | None:
    s = config.load()
    domain = s.get("proxy_domain") or ""
    host = (host or "").split(":")[0].lower()
    if not domain or not host.endswith("." + domain):
        return None
    sub = host[: -len(domain) - 1]
    for a in apps.installed()["apps"]:
        if a.get("address") == sub:
            return a
    return None


def tick() -> None:
    now = time.time()
    inst = [a for a in apps.installed()["apps"] if a.get("sleep_after") and not a.get("trial")]
    if not inst:
        return
    stats = _stats()
    for a in inst:
        n = a["name"]
        if a.get("asleep") or a.get("status") != "running" or n in apps._busy or n in _waking:
            _last_bytes.pop(n, None)
            continue
        st = stats.get(n)
        if not st:
            continue
        prev = _last_bytes.get(n)
        _last_bytes[n] = st["bytes"]
        if prev is None or st["bytes"] - prev > IDLE_BYTES or st["bytes"] < prev:
            _last_active[n] = now
            continue
        if now - _last_active.setdefault(n, now) >= a["sleep_after"] * 60:
            try:
                put_to_sleep(n, st["mem_mb"])
            except Exception:  # noqa: BLE001
                _last_active[n] = now


def summary() -> dict:
    inst = apps.installed()["apps"]
    sleeping = [a for a in inst if a.get("asleep")]
    return {"enabled": sum(1 for a in inst if a.get("sleep_after")),
            "sleeping": [{"name": a["name"], "title": a.get("title", a["name"]),
                          "since": a.get("asleep_since"), "mem_mb": a.get("sleep_mem_mb", 0)} for a in sleeping],
            "saved_mb": sum(a.get("sleep_mem_mb", 0) or 0 for a in sleeping)}


def start_watch() -> None:
    def loop():
        time.sleep(90)
        while True:
            try:
                tick()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(60)
    threading.Thread(target=loop, daemon=True, name="sleep").start()
