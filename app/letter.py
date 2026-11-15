"""Veckobrev från servern: varje söndag kväll en kort, vänlig sammanfattning
på svenska – vad som hänt, vad som är skyddat och vad du kan göra.

Breven sparas (de senaste 12) så att de går att läsa i WizzOS, och skickas
till mobilen via samma kanal som larmen (ntfy/webhook) när larmen är på.
"""
from __future__ import annotations

import json
import threading
import time

from . import alerts, apps, backup, config, nestward, snapshots, system, updater

FILE = config.HOME / "letters.json"
DEFAULTS = {"enabled": True, "weekday": 6, "hour": 18}  # söndag kl 18
WEEK = 7 * 86400
_lock = threading.Lock()


def cfg() -> dict:
    return {**DEFAULTS, **(config.load().get("letter") or {})}


def save_cfg(enabled: bool, weekday: int, hour: int) -> dict:
    if not (0 <= weekday <= 6 and 0 <= hour <= 23):
        raise ValueError("Ogiltig dag eller timme.")
    config.update(letter={"enabled": bool(enabled), "weekday": int(weekday), "hour": int(hour)})
    return cfg()


def _load() -> dict:
    try:
        return json.loads(FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return {"letters": [], "last_week": ""}


def _save(d: dict) -> None:
    d["letters"] = d["letters"][-12:]
    FILE.write_text(json.dumps(d, ensure_ascii=False), "utf-8")


def letters() -> list[dict]:
    return _load()["letters"][::-1]


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def compose(now: float | None = None) -> dict:
    """Skriv veckans brev. Returnerar {title, lines, tip, mood}."""
    now = now or time.time()
    since = now - WEEK
    st = system.stats()
    inst = apps.installed()["apps"]
    kept = [a for a in inst if not a.get("trial")]
    running = [a for a in kept if a.get("status") == "running"]
    lines: list[str] = []
    worries = 0

    # Läget just nu
    if kept and len(running) == len(kept):
        lines.append(f"✅ Alla {_plural(len(kept), 'app', 'appar')} är igång. Servern har varit uppe i {st['uptime'] // 86400} dagar.")
    elif kept:
        down = [a["title"] for a in kept if a.get("status") != "running" and not a.get("user_stopped")]
        lines.append(f"{'⚠️' if down else '✅'} {len(running)} av {len(kept)} appar är igång"
                     + (f" – kolla {', '.join(down)}." if down else " (resten har du stoppat själv)."))
        worries += bool(down)
    else:
        lines.append("Inga appar installerade än – appbutiken väntar på dig.")

    # Backup och bevis
    b = backup.summary()
    bst = backup.state().get("apps", {})
    if not b.get("configured"):
        lines.append("🛡️ Backup är inte inställd. Det är det viktigaste du kan göra för din data.")
        worries += 1
    else:
        failed = [n for n, a in bst.items() if a.get("last_backup", 0) >= since and not a.get("backup_ok")]
        tests = [(n, a) for n, a in bst.items() if a.get("last_test", 0) >= since]
        ok_tests = [n for n, a in tests if a.get("test_ok")]
        txt = f"🛡️ Backup: {_plural(len(kept), 'app', 'appar')} skyddade"
        if b.get("last_run"):
            txt += f", senast {time.strftime('%A %H:%M', time.localtime(b['last_run']['time']))}"
        txt += "."
        if ok_tests:
            txt += f" Återställningen av {', '.join(ok_tests)} provades i veckan och fungerade – din backup är bevisad."
        lines.append(_sv_days(txt))
        if failed:
            lines.append(f"⚠️ Backupen misslyckades för {', '.join(failed)} någon gång i veckan.")
            worries += 1
        if tests and not ok_tests:
            lines.append("⚠️ Veckans återställningstest misslyckades. Öppna Backup och fråga AI vad som hänt.")
            worries += 1
        if b.get("max_gb") and b.get("repo_size"):
            pct = b["repo_size"] / (b["max_gb"] * 1024 ** 3) * 100
            if pct > 70:
                lines.append(f"📦 Backupen använder {pct:.0f} % av taket på {b['max_gb']} GB.")

    # Händelser
    hist = [h for h in alerts._state().get("history", []) if h.get("time", 0) >= since]
    problems = [h for h in hist if h.get("kind") == "problem"]
    solved = [h for h in hist if h.get("kind") == "löst"]
    if problems:
        lines.append(f"🔔 {_plural(len(problems), 'larm', 'larm')} i veckan, {len(solved)} löste sig. "
                     f"Senaste: {problems[-1].get('title', '')}.")
    else:
        lines.append("🔕 Inga larm hela veckan.")

    # Ändringar och ångra
    snaps = []
    for a in inst:
        try:
            snaps += [s for s in snapshots.list_for(a["name"]) if s.get("created", 0) >= since]
        except ValueError:
            pass
    updates = sum(1 for s in snaps if s.get("reason") == "update")
    restores = sum(1 for s in snaps if s.get("reason") == "restore")
    if updates or restores:
        parts = [p for p in (_plural(updates, 'säker uppdatering', 'säkra uppdateringar') if updates else "",
                             _plural(restores, 'ångring', 'ångringar') if restores else "") if p]
        lines.append(f"↶ {' och '.join(parts)} – med en återställningspunkt före varje ändring.")

    try:
        uw = updater.week(since)
    except Exception:  # noqa: BLE001
        uw = {"updated": [], "rolled_back": [], "available": []}
    if uw["updated"]:
        lines.append(f"🌙 Nattens uppdateringar: {', '.join(sorted(set(uw['updated'])))} fick ny version och mår bra.")
    if uw["rolled_back"]:
        lines.append(f"🛟 Skyddsnätet tog emot: {', '.join(sorted(set(uw['rolled_back'])))} backades automatiskt när den nya versionen inte fungerade.")
    if uw["available"]:
        lines.append(f"🆕 Ny version finns för {', '.join(uw['available'])} – uppdatera säkert via appens meny.")

    trials = [a for a in inst if a.get("trial")]
    if trials:
        lines.append(f"🧪 På prov just nu: {', '.join(a['title'] for a in trials)}.")

    # Lagring
    d = st["disk"]
    lines.append(f"💾 Lagring: {d['percent']:.0f} % använt ({_human(d['total'] - d['used'])} ledigt).")
    if d["percent"] >= 85:
        worries += 1

    # Hela hemmet via Nestward
    try:
        nw = nestward.summary()
        if nw.get("ok"):
            lines.append(f"🏠 Hela hemmet (Nestward): {nw['score']}/100 – {nw.get('grade_text', '')}.")
    except Exception:  # noqa: BLE001
        pass

    tip = _tip(b, inst, now)
    mood = "good" if not worries else "warn"
    title = "Veckobrev: allt lugnt 🌿" if not worries else "Veckobrev: några saker att titta på"
    return {"time": int(now), "title": title, "lines": lines, "tip": tip, "mood": mood}


SV_DAYS = {"Monday": "måndag", "Tuesday": "tisdag", "Wednesday": "onsdag", "Thursday": "torsdag",
           "Friday": "fredag", "Saturday": "lördag", "Sunday": "söndag"}


def _sv_days(text: str) -> str:
    for en, sv in SV_DAYS.items():
        text = text.replace(en, sv)
    return text


def _human(b: float) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.0f} {u}" if u in ("B", "KB", "MB") else f"{b:.1f} {u}".replace(".", ",")
        b /= 1024
    return f"{b:.1f} PB".replace(".", ",")


def _tip(b: dict, inst: list[dict], now: float) -> str:
    s = config.load()
    tips = []
    if not b.get("configured"):
        tips.append("Ställ in backup under Backup – det tar två minuter och kan rädda allt.")
    if not s.get("proxy_enabled"):
        tips.append("Ge apparna riktiga adresser med https under Inställningar → Egen adress.")
    if not (config.load().get("nestward") or {}).get("url"):
        tips.append("Har du fler maskiner? Koppla Nestward så ser du hela hemmet på startsidan.")
    tips += [
        "Nyfiken på en app? Tryck på 🧪 Prova i appbutiken – den försvinner spårlöst efteråt om du inte vill behålla den.",
        "Lägg WizzOS på mobilens hemskärm: Inställningar → 📱 WizzOS i mobilen.",
        "Allt du raderar i Filer ligger i papperskorgen i 30 dagar – inget är borta på riktigt direkt.",
        "Ska du byta dator? Backup → 📦 Flytta till ny server tar med allt.",
        "Undrar du över en logg? Tryck på ✨ Förklara loggen, så förklarar AI:n på svenska.",
    ]
    week = int(now // WEEK)
    return tips[week % len(tips)] if tips else ""


def as_text(letter: dict) -> str:
    return "\n".join(letter["lines"]) + (f"\n\n💡 {letter['tip']}" if letter.get("tip") else "")


def send_now(store: bool = True) -> dict:
    letter = compose()
    if store:
        with _lock:
            d = _load()
            d["letters"].append(letter)
            _save(d)
    c = alerts.cfg()
    letter["sent"] = False
    if c.get("enabled"):
        try:
            alerts.send(letter["title"], as_text(letter), priority=2,
                        tags="love_letter" if letter["mood"] == "good" else "mailbox_with_mail", c=c)
            letter["sent"] = True
        except Exception as e:  # noqa: BLE001
            letter["error"] = str(e)
    return letter


_started = False


def start_scheduler() -> None:
    global _started
    if _started:
        return
    _started = True

    def loop():
        time.sleep(120)
        while True:
            try:
                c = cfg()
                lt = time.localtime()
                week = time.strftime("%G-%V", lt)
                if c["enabled"] and lt.tm_wday == c["weekday"] and lt.tm_hour == c["hour"]:
                    with _lock:
                        due = _load().get("last_week") != week
                    if due:
                        send_now()
                        with _lock:
                            d = _load()
                            d["last_week"] = week
                            _save(d)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(300)

    threading.Thread(target=loop, daemon=True, name="weekly-letter").start()
