"""Serverkortet: data till en delbar bild av din server (ritas i webbläsaren),
och appikoner som hämtas via servern så att bilden går att spara.

Ingenting här avslöjar adresser, IP eller nycklar – bara antal, namn och
bevis. Servernamn och appnamn kan döljas i gränssnittet."""
from __future__ import annotations

import hashlib
import json
import time

import httpx

from . import __version__, apps, backup, config, nestward, snapshots, system

ICON_DIR = config.HOME / "icons"
ICON_MAX = 512 * 1024
ICON_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif", "image/svg+xml", "image/x-icon",
              "image/vnd.microsoft.icon")


def data() -> dict:
    st = system.stats()
    inst = [a for a in apps.installed()["apps"] if not a.get("trial")]
    b = backup.summary()
    bst = backup.state().get("apps", {})
    tests = [a.get("last_test", 0) for a in bst.values() if a.get("test_ok")]
    undo = 0
    for a in inst:
        try:
            undo += sum(1 for s in snapshots.list_for(a["name"]) if s.get("reason") in ("update", "restore", "settings"))
        except ValueError:
            pass
    since = None
    s = config.load()
    if s.get("backup", {}).get("configured_at"):
        since = int(s["backup"]["configured_at"])
    nw = {}
    try:
        n = nestward.summary()
        if n.get("ok"):
            nw = {"score": n["score"], "machines": n["machines"]}
    except Exception:  # noqa: BLE001
        pass
    return {
        "name": s.get("server_name") or system.hostname(),
        "apps": [{"name": a["name"], "title": a.get("title", a["name"]), "running": a.get("status") == "running"}
                 for a in inst],
        "uptime_days": st["uptime"] // 86400,
        "disk_total": st["disk"]["total"],
        "backup": {"configured": bool(b.get("configured")),
                   "proven": int(max(tests)) if tests else None,
                   "since": since},
        "undo_points": undo,
        "nestward": nw,
        "version": __version__,
        "date": int(time.time()),
    }


def icon(name: str) -> tuple[bytes, str]:
    """Appens ikon (sparad lokalt efter första hämtningen)."""
    d = apps.app_dir(name)
    meta = json.loads((d / "meta.json").read_text("utf-8"))
    url = meta.get("icon", "")
    if not url.startswith("https://"):
        raise ValueError("Ingen ikon.")
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()[:24]
    f, t = ICON_DIR / key, ICON_DIR / f"{key}.type"
    if f.exists() and t.exists():
        return f.read_bytes(), t.read_text()
    with httpx.stream("GET", url, timeout=10, follow_redirects=True) as r:
        ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
        if r.status_code != 200 or ctype not in ICON_TYPES:
            raise ValueError("Ikonen gick inte att hämta.")
        body = b""
        for chunk in r.iter_bytes():
            body += chunk
            if len(body) > ICON_MAX:
                raise ValueError("Ikonen är för stor.")
    f.write_bytes(body)
    t.write_text(ctype)
    return body, ctype
