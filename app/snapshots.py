"""Återställningspunkter ("Ångra-knappen").

En punkt sparar allt som behövs för att få tillbaka en app exakt som den var:
  app.tar.gz   compose-fil, meta.json och .env
  data.tar.gz  appens data i /DATA/AppData/<namn>
  info.json    när, varför, version och vilka Docker-images som användes

Imagen sparas genom att den gamla versionen får en extra tagg
(wizzos-undo/<app>:<punkt>-<tjänst>), så Docker raderar den inte när en
uppdatering hämtar en ny. Vid återställning flyttas taggen tillbaka.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import apps, config

SNAP_DIR = config.HOME / "snapshots"
KEEP_DAYS = 30
KEEP_PER_APP = 6
ID_RE = re.compile(r"^\d{8}-\d{6}$")
REASONS = {
    "manual": "Sparad för hand",
    "update": "Före uppdatering",
    "settings": "Före ändrade inställningar",
    "uninstall": "Före borttagning",
    "restore": "Före återställning",
}


def _app_snaps(name: str) -> Path:
    apps.app_dir(name)  # kontrollerar namnet
    return SNAP_DIR / name


def _snap_path(name: str, sid: str) -> Path:
    if not ID_RE.match(sid):
        raise ValueError("Ogiltig återställningspunkt")
    return _app_snaps(name) / sid


def data_dir(name: str) -> Path:
    return config.DATA_ROOT / "AppData" / name


def _dir_size(path: Path, limit: int | None = None) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
        if limit and total > limit:
            return total
    return total


def _run(cmd: list[str], log=None) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip()
        if log:
            log(msg)
        raise RuntimeError(msg or "Kommandot misslyckades")
    return r.stdout


def service_images(name: str) -> dict:
    """{tjänst: {"ref": "bild:tagg", "id": "sha256:..."}} för appens containrar."""
    out = {}
    try:
        rows = _run(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={name}",
                     "--format", '{{.ID}}\t{{.Label "com.docker.compose.service"}}'])
    except RuntimeError:
        return out
    for line in rows.splitlines():
        if "\t" not in line:
            continue
        cid, svc = line.split("\t", 1)
        try:
            img_id, ref = _run(["docker", "inspect", "-f", "{{.Image}}\t{{.Config.Image}}",
                                cid]).strip().split("\t", 1)
        except (RuntimeError, ValueError):
            continue
        out[svc] = {"ref": ref, "id": img_id}
    return out


def is_running(name: str) -> bool:
    try:
        rows = _run(["docker", "ps", "--filter", f"label=com.docker.compose.project={name}",
                     "--format", "{{.ID}}"])
    except RuntimeError:
        return False
    return bool(rows.strip())


# ---------- skapa ----------

def create(name: str, reason: str, log, *, stop: bool = True, start_after: bool = True,
           images: dict | None = None) -> dict:
    """Spara en återställningspunkt. Appen stoppas en kort stund medan datan
    kopieras, så att databaser inte sparas mitt i en skrivning."""
    d = apps.app_dir(name)
    if not d.exists():
        raise ValueError("Appen finns inte.")
    s = config.load()
    max_bytes = int(float(s.get("undo_max_gb", 20)) * 1024 ** 3)
    sid = time.strftime("%Y%m%d-%H%M%S")
    path = _snap_path(name, sid)
    while path.exists():
        time.sleep(1)
        sid = time.strftime("%Y%m%d-%H%M%S")
        path = _snap_path(name, sid)
    path.mkdir(parents=True)
    meta = json.loads((d / "meta.json").read_text("utf-8"))
    info = {"id": sid, "app": name, "title": meta.get("title", name), "reason": reason,
            "created": time.time(), "version": meta.get("version", ""), "images": {},
            "data": False, "size": 0, "note": ""}
    try:
        log(f"Sparar återställningspunkt ({REASONS.get(reason, reason).lower()}) ...")
        # 1. Programversionen
        images = images if images is not None else service_images(name)
        for svc, img in images.items():
            safe_svc = re.sub(r"[^a-z0-9_.-]", "-", svc.lower())
            tag = f"wizzos-undo/{name}:{sid}-{safe_svc}"
            try:
                _run(["docker", "tag", img["id"], tag])
                info["images"][svc] = {**img, "saved": tag}
            except RuntimeError:
                log(f"Kunde inte spara versionen av {svc}.")
        # 2. Inställningar
        _run(["tar", "-czf", str(path / "app.tar.gz"), "-C", str(config.APPS_DIR), name])
        # 3. Data
        dd = data_dir(name)
        if dd.exists():
            size = _dir_size(dd, limit=max_bytes)
            free = shutil.disk_usage(config.DATA_ROOT).free
            if size > max_bytes:
                info["note"] = "Datan var för stor för att sparas (över gränsen i inställningarna)."
                log(info["note"])
            elif size * 1.1 + 512 * 1024 ** 2 > free:
                info["note"] = "För lite ledigt diskutrymme för att spara datan."
                log(info["note"])
            else:
                was_running = stop and is_running(name)
                if was_running:
                    log("Pausar appen medan datan kopieras ...")
                    _run(apps._compose(name, "stop"), log)
                try:
                    _run(["tar", "--numeric-owner", "-I", "gzip -1", "-cf",
                          str(path / "data.tar.gz"), "-C", str(dd.parent), dd.name], log)
                    info["data"] = True
                finally:
                    if was_running and start_after:
                        _run(apps._compose(name, "start"), log)
        info["size"] = _dir_size(path)
        (path / "info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), "utf-8")
        log(f"Återställningspunkten är sparad ({_human(info['size'])}).")
    except Exception:
        _remove(name, sid)
        raise
    prune(name)
    return info


def _human(b: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.0f} {u}"
        b /= 1024
    return f"{b:.1f} TB"


# ---------- lista / ta bort ----------

def list_for(name: str) -> list[dict]:
    base = _app_snaps(name)
    out = []
    if base.exists():
        for p in base.iterdir():
            f = p / "info.json"
            if ID_RE.match(p.name) and f.exists():
                try:
                    out.append(json.loads(f.read_text("utf-8")))
                except ValueError:
                    pass
    out.sort(key=lambda i: i["created"], reverse=True)
    return out


def removed_apps() -> list[dict]:
    """Appar som har avinstallerats men fortfarande har en återställningspunkt."""
    out = []
    if not SNAP_DIR.exists():
        return out
    for p in sorted(SNAP_DIR.iterdir()):
        if not p.is_dir() or (config.APPS_DIR / p.name).exists():
            continue
        try:
            snaps = list_for(p.name)
        except ValueError:
            continue
        if snaps:
            last = snaps[0]
            icon = ""
            try:
                meta = _read_meta_from_tar(p / last["id"] / "app.tar.gz", p.name)
                icon = meta.get("icon", "")
            except Exception:  # noqa: BLE001
                pass
            out.append({"name": p.name, "title": last["title"], "icon": icon,
                        "removed": last["created"], "snapshot": last["id"],
                        "expires": last["created"] + KEEP_DAYS * 86400})
    return out


def _read_meta_from_tar(tar_path: Path, name: str) -> dict:
    import tarfile
    with tarfile.open(tar_path) as t:
        f = t.extractfile(f"{name}/meta.json")
        return json.loads(f.read().decode("utf-8")) if f else {}


def _remove(name: str, sid: str) -> None:
    path = _snap_path(name, sid)
    info_f = path / "info.json"
    if info_f.exists():
        try:
            for img in json.loads(info_f.read_text("utf-8")).get("images", {}).values():
                subprocess.run(["docker", "rmi", img["saved"]], capture_output=True)
        except (ValueError, KeyError):
            pass
    shutil.rmtree(path, ignore_errors=True)
    parent = path.parent
    if parent.exists() and not any(parent.iterdir()):
        parent.rmdir()


def delete(name: str, sid: str) -> None:
    if not _snap_path(name, sid).exists():
        raise ValueError("Återställningspunkten finns inte.")
    _remove(name, sid)


def prune(name: str | None = None) -> None:
    """Rensa punkter äldre än 30 dagar, och spara högst sex per app."""
    if not SNAP_DIR.exists():
        return
    names = [name] if name else [p.name for p in SNAP_DIR.iterdir() if p.is_dir()]
    cutoff = time.time() - KEEP_DAYS * 86400
    for n in names:
        try:
            snaps = list_for(n)
        except ValueError:
            continue
        installed = (config.APPS_DIR / n).exists()
        for i, s in enumerate(snaps):
            # Den senaste punkten för en borttagen app är "papperskorgen":
            # den sparas i 30 dagar men inte längre.
            too_many = installed and i >= KEEP_PER_APP
            if s["created"] < cutoff or too_many or (not installed and i > 0):
                _remove(n, s["id"])


_cleaner_started = False


def start_cleaner() -> None:
    global _cleaner_started
    if _cleaner_started:
        return
    _cleaner_started = True

    def loop():
        while True:
            try:
                prune()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(6 * 3600)
    threading.Thread(target=loop, daemon=True).start()


# ---------- återställa ----------

def restore(name: str, sid: str, log, *, safety: bool = True) -> None:
    """Gör appen exakt som den var vid punkten. Med safety=True sparas
    först nuläget, så att även återställningen går att ångra."""
    path = _snap_path(name, sid)
    info_f = path / "info.json"
    if not info_f.exists():
        raise ValueError("Återställningspunkten finns inte.")
    info = json.loads(info_f.read_text("utf-8"))
    d = config.APPS_DIR / name
    if safety and d.exists():
        create(name, "restore", log, start_after=False)
    if d.exists():
        log("Stoppar appen ...")
        subprocess.run(apps._compose(name, "down", "--remove-orphans"), capture_output=True)

    log("Lägger tillbaka inställningarna ...")
    stamp = str(int(time.time()))
    old_app = config.APPS_DIR / f".{name}.old-{stamp}"
    if d.exists():
        d.rename(old_app)
    try:
        _run(["tar", "-xzf", str(path / "app.tar.gz"), "-C", str(config.APPS_DIR)], log)
    except Exception:
        if old_app.exists():
            old_app.rename(d)
        raise
    shutil.rmtree(old_app, ignore_errors=True)

    if info.get("data") and (path / "data.tar.gz").exists():
        log("Lägger tillbaka datan ...")
        dd = data_dir(name)
        old_data = dd.parent / f".{name}.old-{stamp}"
        if dd.exists():
            dd.rename(old_data)
        try:
            dd.parent.mkdir(parents=True, exist_ok=True)
            _run(["tar", "--numeric-owner", "--same-owner", "-I", "gzip -d", "-xf",
                  str(path / "data.tar.gz"), "-C", str(dd.parent)], log)
        except Exception:
            shutil.rmtree(dd, ignore_errors=True)
            if old_data.exists():
                old_data.rename(dd)
            raise
        shutil.rmtree(old_data, ignore_errors=True)
    elif info.get("note"):
        log("Obs: " + info["note"] + " Datan lämnas som den är.")

    for svc, img in info.get("images", {}).items():
        try:
            _run(["docker", "tag", img["saved"], img["ref"]])
            log(f"Tillbaka till tidigare version av {svc}.")
        except RuntimeError:
            log(f"Hittade inte den sparade versionen av {svc}, använder den som finns.")

    log("Startar appen ...")
    _run(apps._compose(name, "up", "-d", "--remove-orphans", "--pull", "never"), log)
