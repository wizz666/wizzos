"""WizzOS uppdaterar sig själv – med samma skyddsnät som apparna.

1. Kollar senaste versionen på GitHub (releases/latest).
2. Startar en liten hjälpcontainer (med den NUVARANDE versionens program, så att
   en trasig ny version aldrig kan förstöra sin egen uppdatering).
3. Hjälparen hämtar nya images, byter WIZZOS_VERSION i .env, startar om
   WizzOS och väntar på att den svarar med rätt version.
4. Svarar den inte inom 2 minuter backar hjälparen till förra versionen.

Resultatet skrivs i /DATA/WizzOS/selfupdate.json och visas/larmas vid nästa start.
Körs manuellt (Inställningar → WizzOS-version) eller på natten med appuppdateringarna.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

from . import __version__, config

REPO = os.environ.get("WIZZOS_REPO", "wizz666/wizzos")
IMAGE = os.environ.get("WIZZOS_IMAGE", f"ghcr.io/{REPO}")
HOST_DIR = os.environ.get("WIZZOS_HOST_DIR", "/opt/wizzos")
STATUS = config.HOME / "selfupdate.json"
_cache = {"t": 0, "v": None}


def _ver(s: str) -> tuple:
    nums = re.findall(r"\d+", s or "")
    return tuple(int(n) for n in nums[:3]) if nums else (0,)


def cfg() -> dict:
    return {"auto": True, **(config.load().get("selfupdate") or {})}


def save(auto: bool) -> dict:
    config.update(selfupdate={**cfg(), "auto": bool(auto)})
    return cfg()


def latest(force: bool = False) -> dict:
    """{'tag','version','notes','url','published'} eller {'error'}."""
    if not force and _cache["v"] and time.time() - _cache["t"] < 3600:
        return _cache["v"]
    import httpx
    try:
        r = httpx.get(f"https://api.github.com/repos/{REPO}/releases/latest", timeout=10,
                      headers={"Accept": "application/vnd.github+json", "User-Agent": "WizzOS"})
        if r.status_code == 404:
            res = {"error": "Inga versioner publicerade än."}
        else:
            r.raise_for_status()
            j = r.json()
            res = {"tag": j["tag_name"], "version": j["tag_name"].lstrip("v"), "notes": (j.get("body") or "")[:4000],
                   "url": j.get("html_url", ""), "published": j.get("published_at", "")}
    except Exception as e:  # noqa: BLE001
        res = {"error": f"Kunde inte nå GitHub ({type(e).__name__})."}
    _cache.update(t=time.time(), v=res)
    return res


def status() -> dict:
    try:
        last = json.loads(STATUS.read_text("utf-8"))
    except (OSError, ValueError):
        last = {}
    lt = latest()
    running = _helper_running()
    out = {"current": __version__, "image_tag": os.environ.get("WIZZOS_VERSION", ""), "latest": lt, "last": last,
           "running": running, "auto": cfg()["auto"], "managed": _managed()}
    out["available"] = bool(lt.get("version")) and _ver(lt["version"]) > _ver(__version__)
    return out


def _managed() -> bool:
    """Bara installationer som kör färdiga images kan uppdatera sig själva."""
    v = os.environ.get("WIZZOS_VERSION", "")
    return bool(v) and v != "dev" and os.path.exists("/var/run/docker.sock")


def _helper_running() -> bool:
    r = subprocess.run(["docker", "ps", "-q", "--filter", "name=^wizzos-updater$"], capture_output=True, text=True)
    return bool(r.stdout.strip())


def _own_image() -> str:
    r = subprocess.run(["docker", "inspect", "-f", "{{.Image}}", os.environ.get("HOSTNAME", "wizzos")],
                       capture_output=True, text=True)
    img = r.stdout.strip()
    if not img:
        r = subprocess.run(["docker", "inspect", "-f", "{{.Image}}", "wizzos"], capture_output=True, text=True)
        img = r.stdout.strip()
    return img


def start(tag: str | None = None) -> dict:
    if not _managed():
        raise ValueError("Den här installationen bygger WizzOS från källkoden och uppdateras med installationsskriptet.")
    if _helper_running():
        raise ValueError("En uppdatering pågår redan.")
    lt = latest(force=True)
    tag = tag or lt.get("tag")
    if not tag:
        raise ValueError(lt.get("error") or "Ingen ny version hittades.")
    img = _own_image()
    if not img:
        raise ValueError("Hittar inte WizzOS egen container.")
    port = os.environ.get("WIZZOS_PORT", "8899")
    listen = os.environ.get("WIZZOS_LISTEN_IP", "0.0.0.0")
    cmd = ["docker", "run", "-d", "--rm", "--name", "wizzos-updater", "--network", "host",
           "-v", "/var/run/docker.sock:/var/run/docker.sock", "-v", f"{HOST_DIR}:{HOST_DIR}",
           "-v", f"{config.HOME}:{config.HOME}", "-e", f"WIZZOS_DATA={config.DATA_ROOT}",
           img, "python", "-m", "app.selfupdate", "helper", HOST_DIR, tag.lstrip("v"),
           __version__, listen if listen not in ("0.0.0.0", "") else "127.0.0.1", port]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Kunde inte starta uppdateringen: {r.stderr.strip()[:200]}")
    _write({"state": "running", "from": __version__, "to": tag.lstrip("v"), "time": int(time.time())})
    return {"ok": True, "to": tag.lstrip("v")}


def _write(d: dict) -> None:
    tmp = STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), "utf-8")
    tmp.replace(STATUS)


def announce_on_start() -> None:
    """Vid start: berätta om en uppdatering som just gjorts (eller backats)."""
    try:
        d = json.loads(STATUS.read_text("utf-8"))
    except (OSError, ValueError):
        return
    if d.get("announced") or d.get("state") not in ("done", "rolled_back", "failed"):
        return
    from . import alerts
    try:
        if d["state"] == "done":
            alerts.notify("WizzOS är uppdaterad", f"Version {d['from']} → {d['to']}. Allt fungerar.")
        else:
            alerts.notify("WizzOS-uppdateringen backades",
                          f"Version {d['to']} startade inte som den skulle ({d.get('why', 'okänt fel')}). "
                          f"WizzOS kör {d['from']} igen – ingenting gick förlorat.", priority=4)
    finally:
        d["announced"] = True
        _write(d)


def nightly() -> str:
    """Anropas av de nattliga uppdateringarna."""
    if not (cfg()["auto"] and _managed()):
        return ""
    s = status()
    if s["available"] and not s["running"]:
        start(s["latest"]["tag"])
        return f"WizzOS uppdateras till {s['latest']['version']}."
    return ""


# ---------- hjälparen (körs i en egen container) ----------

def _helper(host_dir: str, to: str, frm: str, ip: str, port: str) -> None:
    import urllib.request
    envf = os.path.join(host_dir, ".env")
    compose = ["docker", "compose", "--project-directory", host_dir, "-f", os.path.join(host_dir, "docker-compose.yml")]

    def log(state, **kw):
        _write({"state": state, "from": frm, "to": to, "time": int(time.time()), **kw})

    def set_version(v):
        lines = open(envf, encoding="utf-8").read().splitlines() if os.path.exists(envf) else []
        lines = [ln for ln in lines if not ln.startswith("WIZZOS_VERSION=")] + [f"WIZZOS_VERSION={v}"]
        with open(envf, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def healthy(want, secs=120):
        end = time.time() + secs
        while time.time() < end:
            try:
                with urllib.request.urlopen(f"http://{ip}:{port}/api/state", timeout=5) as r:
                    if json.loads(r.read()).get("version") == want:
                        return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(3)
        return False

    old_env = open(envf, encoding="utf-8").read() if os.path.exists(envf) else ""
    old_ver = next((ln.split("=", 1)[1] for ln in old_env.splitlines() if ln.startswith("WIZZOS_VERSION=")), frm)
    log("running", step="hämtar")
    set_version(to)
    p = subprocess.run(compose + ["pull", "-q", "--ignore-pull-failures"], capture_output=True, text=True)
    imgs = subprocess.run(compose + ["config", "--images"], capture_output=True, text=True).stdout.split()
    missing = [i for i in imgs if subprocess.run(["docker", "image", "inspect", i], capture_output=True).returncode != 0]
    if p.returncode != 0 or missing:
        with open(envf, "w", encoding="utf-8") as f:
            f.write(old_env)
        log("failed", why=f"kunde inte hämta {', '.join(missing) or ''} {(p.stderr or '').strip()[-160:]}".strip())
        return
    log("running", step="startar")
    subprocess.run(compose + ["up", "-d", "--remove-orphans"], capture_output=True, text=True)
    if healthy(to):
        log("done")
        return
    # Backa
    with open(envf, "w", encoding="utf-8") as f:
        f.write(old_env)
    subprocess.run(compose + ["up", "-d", "--remove-orphans"], capture_output=True, text=True)
    log("rolled_back", why="svarade inte inom 2 minuter", back_ok=healthy(frm, 90), back_to=old_ver)


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "helper":
    _helper(*sys.argv[2:7])
