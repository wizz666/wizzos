"""Nattliga uppdateringar med skyddsnät.

Varje natt (standard kl 04:30, efter backupen) går WizzOS igenom apparna:
hämtar ny version, tar en återställningspunkt, startar, kontrollerar att appen
mår bra – och backar automatiskt om den inte gör det. Du får en notis med
resultatet, och veckobrevet berättar vad som hänt.

Två lägen:
- "auto":   uppdatera själv (med skyddsnät).
- "notify": bara ta reda på vilka appar som har en ny version och säga till.

Hoppar över appar på prov, appar du stoppat själv och appar du undantagit
("Uppdatera inte automatiskt" i appens meny).
"""
from __future__ import annotations

import json
import subprocess
import threading
import time

from . import alerts, apps, config, jobs

STATE = config.HOME / "updates.json"
DEFAULTS = {"enabled": True, "mode": "auto", "time": "04:30"}
_lock = threading.Lock()


def cfg() -> dict:
    return {**DEFAULTS, **(config.load().get("updates") or {})}


def save_cfg(enabled: bool, mode: str, at: str) -> dict:
    import re
    if mode not in ("auto", "notify"):
        raise ValueError("Okänt läge.")
    if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", at or ""):
        raise ValueError("Skriv tiden som 04:30.")
    config.update(updates={"enabled": bool(enabled), "mode": mode, "time": at})
    return cfg()


def state() -> dict:
    try:
        return json.loads(STATE.read_text("utf-8"))
    except (OSError, ValueError):
        return {"runs": [], "available": {}, "last_night": ""}


def _save(s: dict) -> None:
    s["runs"] = s.get("runs", [])[-30:]
    STATE.write_text(json.dumps(s, ensure_ascii=False), "utf-8")


def set_auto(name: str, on: bool) -> dict:
    d = apps.app_dir(name)
    meta = json.loads((d / "meta.json").read_text("utf-8"))
    if on:
        meta.pop("no_auto_update", None)
    else:
        meta["no_auto_update"] = True
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
    return {"app": name, "auto": on}


def _candidates() -> tuple[list[dict], list[tuple[str, str]]]:
    todo, skipped = [], []
    for a in apps.installed()["apps"]:
        if a.get("trial"):
            skipped.append((a["title"], "på prov"))
        elif a.get("user_stopped") or a.get("status") == "stopped":
            skipped.append((a["title"], "stoppad"))
        elif a.get("no_auto_update"):
            skipped.append((a["title"], "undantagen"))
        else:
            todo.append(a)
    return todo, skipped


def _has_new(name: str, log) -> bool:
    """Hämta (pull) och jämför – utan att starta något."""
    from . import snapshots
    before = snapshots.service_images(name)
    jobs.run_cmd(apps._compose(name, "pull", "--quiet"), log)
    for img in before.values():
        try:
            if apps.docker("image", "inspect", "-f", "{{.Id}}", img["ref"]).strip() != img["id"]:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _claim(name: str) -> bool:
    with apps._busy_lock:
        if name in apps._busy:
            return False
        apps._busy.add(name)
        return True


def _release(name: str) -> None:
    with apps._busy_lock:
        apps._busy.discard(name)


def run(log, mode: str | None = None) -> dict:
    mode = mode or cfg()["mode"]
    todo, skipped = _candidates()
    log(f"{'Uppdaterar' if mode == 'auto' else 'Letar efter nya versioner för'} {len(todo)} appar"
        + (f" (hoppar över {len(skipped)}: " + ", ".join(f"{t} – {w}" for t, w in skipped) + ")" if skipped else "") + ".")
    run_ = {"time": int(time.time()), "mode": mode, "updated": [], "rolled_back": [], "available": [],
            "unchanged": 0, "failed": [], "skipped": [t for t, _ in skipped]}
    avail = {}
    for a in todo:
        name, title = a["name"], a.get("title", a["name"])
        if not _claim(name):
            log(f"{title}: upptagen just nu – tar den nästa natt.")
            continue
        try:
            log(f"—— {title} ——")
            if mode == "notify":
                if _has_new(name, log):
                    run_["available"].append(title)
                    avail[name] = int(time.time())
                    log(f"{title}: ny version finns.")
                else:
                    run_["unchanged"] += 1
                continue
            r = apps.do_update(name, log, notify=False)
            if not r["changed"]:
                run_["unchanged"] += 1
            elif r["ok"]:
                run_["updated"].append({"title": title, "from": r.get("from", ""), "to": r.get("to", "")})
            else:
                run_["rolled_back"].append({"title": title, "why": r.get("why", ""), "back_ok": r.get("back_ok", False)})
        except Exception as e:  # noqa: BLE001
            log(f"{title}: kunde inte kontrolleras – {e}")
            run_["failed"].append(title)
        finally:
            _release(name)
    if mode == "auto" and (run_["updated"] or run_["rolled_back"]):
        # Rensa bara lösa lager utan namn. Gamla versioner som behövs för ångra
        # är taggade av återställningspunkterna och rörs inte.
        subprocess.run(["docker", "image", "prune", "-f"], capture_output=True)
    with _lock:
        s = state()
        s["runs"].append(run_)
        if mode == "notify":
            s["available"] = avail
        else:
            s["available"] = {}
        _save(s)
    _notify(run_)
    log(summary_text(run_))
    return run_


def summary_text(r: dict) -> str:
    parts = []
    if r["updated"]:
        parts.append("Uppdaterade: " + ", ".join(u["title"] + (f" ({u['to']})" if u.get("to") else "") for u in r["updated"]) + ".")
    if r["rolled_back"]:
        parts.append("Backades automatiskt (fungerade inte): " + ", ".join(x["title"] for x in r["rolled_back"]) + ".")
    if r["available"]:
        parts.append("Ny version finns för: " + ", ".join(r["available"]) + ". Uppdatera säkert via appens meny.")
    if r["failed"]:
        parts.append("Kunde inte kontrolleras: " + ", ".join(r["failed"]) + ".")
    if not parts:
        parts.append(f"Allt var redan senaste version ({r['unchanged']} appar kontrollerade).")
    return " ".join(parts)


def _notify(r: dict) -> None:
    if not (r["updated"] or r["rolled_back"] or r["available"] or r["failed"]):
        return  # tyst natt = inget meddelande
    title = ("Nattens uppdateringar: något backades" if r["rolled_back"]
             else "Nya versioner finns" if r["available"] and not r["updated"]
             else "Nattens uppdateringar klara")
    try:
        alerts.notify(title, summary_text(r), 4 if r["rolled_back"] else 2)
    except Exception:  # noqa: BLE001
        pass


def run_now(mode: str | None = None) -> str:
    return jobs.start("Kontrollerar uppdateringar", lambda log: run(log, mode))


def week(since: float) -> dict:
    """Sammanställning för veckobrevet."""
    out = {"updated": [], "rolled_back": [], "available": []}
    for r in state().get("runs", []):
        if r.get("time", 0) < since:
            continue
        out["updated"] += [u["title"] for u in r.get("updated", [])]
        out["rolled_back"] += [x["title"] for x in r.get("rolled_back", [])]
        out["available"] = r.get("available", []) or out["available"]
    return out


_started = False


def start_scheduler() -> None:
    global _started
    if _started:
        return
    _started = True

    def loop():
        time.sleep(90)
        while True:
            try:
                c = cfg()
                if c["enabled"]:
                    h, m = map(int, c["time"].split(":"))
                    lt = time.localtime()
                    today = time.strftime("%Y-%m-%d", lt)
                    minutes_past = (lt.tm_hour * 60 + lt.tm_min) - (h * 60 + m)
                    with _lock:
                        done = state().get("last_night") == today
                    # Kör inom två timmar efter utsatt tid (missas den, t.ex. efter
                    # en omstart, körs den inte mitt på dagen).
                    if not done and 0 <= minutes_past <= 120:
                        with _lock:
                            s = state()
                            s["last_night"] = today
                            _save(s)
                        def night(log):
                            r = run(log)
                            # Sist: WizzOS själv (startar om sig, så allt annat görs först).
                            try:
                                from . import selfupdate
                                msg = selfupdate.nightly()
                                if msg:
                                    log(msg)
                            except Exception as e:  # noqa: BLE001
                                log(f"WizzOS själv: {e}")
                            return r
                        jobs.start("Nattliga uppdateringar", night)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(60)

    threading.Thread(target=loop, daemon=True, name="nightly-updates").start()
