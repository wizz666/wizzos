"""Prova först: installera en app på prov. När provtiden är slut tas den bort
spårlöst – containrar, volymer, mapparna som skapades och programmen som
hämtades. Tryck Behåll så blir den en vanlig app.

Provappar säkerhetskopieras inte, får inga larm och ger inga
återställningspunkter (det finns inget att spara)."""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import apps, config, jobs, proxy

MAX_MINUTES = 24 * 60
CHOICES = (30, 60, 180)


def _meta_file(name: str) -> Path:
    return apps.app_dir(name) / "meta.json"


def _meta(name: str) -> dict:
    return json.loads(_meta_file(name).read_text("utf-8"))


def _save(name: str, meta: dict) -> None:
    _meta_file(name).write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")


def is_trial(name: str) -> bool:
    try:
        return bool(_meta(name).get("trial"))
    except (OSError, ValueError):
        return False


def trial_names() -> set[str]:
    out = set()
    if config.APPS_DIR.exists():
        for d in config.APPS_DIR.iterdir():
            try:
                if json.loads((d / "meta.json").read_text("utf-8")).get("trial"):
                    out.add(d.name)
            except (OSError, ValueError):
                continue
    return out


def remove_dirs(dirs: list[str], log) -> None:
    """Ta bara bort mappar som provinstallationen själv skapade, och bara
    under /DATA (aldrig /DATA själv eller WizzOS egna mappar)."""
    root = config.DATA_ROOT.resolve()
    home = config.HOME.resolve()
    for d in dirs or []:
        p = Path(d).resolve()
        if p == root or root not in p.parents or p == home or home in p.parents:
            continue
        if p.exists():
            log(f"Raderar {p}")
            shutil.rmtree(p, ignore_errors=True)


def keep(name: str) -> dict:
    m = _meta(name)
    if not m.get("trial"):
        raise ValueError("Appen är inte på prov.")
    m.pop("trial")
    _save(name, m)
    return {"app": name}


def extend(name: str, minutes: int) -> dict:
    m = _meta(name)
    t = m.get("trial")
    if not t:
        raise ValueError("Appen är inte på prov.")
    minutes = max(5, min(int(minutes), MAX_MINUTES))
    t["until"] = min(max(t["until"], int(time.time())) + minutes * 60, t["started"] + MAX_MINUTES * 60)
    _save(name, m)
    return {"until": t["until"]}


def _discard(name: str, log) -> dict:
    m = _meta(name)
    t = m.get("trial") or {}
    title = m.get("title", name)
    log(f"Tar bort provappen {title} spårlöst ...")
    try:
        jobs.run_cmd(apps._compose(name, "down", "-v", "--remove-orphans"), log)
    except Exception as e:  # noqa: BLE001
        log(f"Kunde inte stoppa allt ({e}), fortsätter ändå.")
    remove_dirs(t.get("dirs", []), log)
    for img in t.get("images", []):
        r = subprocess.run(["docker", "image", "rm", img], capture_output=True, text=True)
        if r.returncode == 0:
            log(f"Tog bort programmet {img}")
    # Återställningspunkter (t.ex. från en uppdatering under provet) och deras
    # sparade programversioner ska inte heller finnas kvar.
    from . import snapshots
    shutil.rmtree(snapshots._app_snaps(name), ignore_errors=True)
    tags = subprocess.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}", f"wizzos-undo/{name}"],
                          capture_output=True, text=True).stdout.split()
    for tag in tags:
        subprocess.run(["docker", "image", "rm", tag], capture_output=True)
    shutil.rmtree(apps.app_dir(name), ignore_errors=True)
    proxy.safe_apply()
    log(f"Klart – {title} är borta och ingenting finns kvar.")
    return {"app": name}


def end(name: str) -> str:
    if not is_trial(name):
        raise ValueError("Appen är inte på prov.")
    title = _meta(name).get("title", name)
    return apps.guarded_job(name, f"Avslutar provet av {title}", lambda log: _discard(name, log))


_started = False


def start_reaper() -> None:
    global _started
    if _started:
        return
    _started = True

    def loop():
        time.sleep(30)
        while True:
            now = time.time()
            for name in trial_names():
                try:
                    m = _meta(name)
                    if m["trial"]["until"] <= now:
                        try:
                            end(name)
                        except ValueError:
                            pass  # upptagen just nu – nästa varv
                        try:
                            from . import alerts
                            alerts.notify("Provtiden är slut",
                                          f"{m.get('title', name)} är borttagen spårlöst. Installera den igen när du vill.", priority=2)
                        except Exception:  # noqa: BLE001
                            pass
                except Exception:  # noqa: BLE001
                    continue
            time.sleep(30)

    threading.Thread(target=loop, daemon=True, name="trial-reaper").start()
