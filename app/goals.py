"""Paket efter mål: välj vad du vill uppnå – inte vilken app.

"Familjens fotoalbum" i stället för "Immich". WizzOS kollar att servern klarar
appen (minne, processor, https), installerar den (gärna på prov först), tar med
den i nattlig backup och uppdateringar, och visar sedan en kort guide på
svenska: första inloggningen, mobilappen och vad man gör härnäst.
"""
from __future__ import annotations

import re

import psutil

from . import apps, config, proxy, store

GOALS = [
    {
        "id": "photos", "emoji": "📸", "title": "Familjens fotoalbum",
        "pitch": "Alla foton och filmer från familjens mobiler samlas automatiskt på servern – som Google Foto, fast privat. "
                 "Ansiktsigenkänning, sök efter \"hund\" eller \"strand\", delade album.",
        "app": "casaos/Immich", "app_title": "Immich", "min_ram_gb": 4, "disk": "Gott om plats – räkna med 10–100 GB per person.",
        "steps": [
            "Öppna Immich och skapa ditt konto. Det första kontot blir administratör.",
            "Ladda ner appen Immich i mobilen (App Store eller Google Play) och ange serverns adress nedan.",
            "Slå på Säkerhetskopiera i mobilappen – nya foton laddas då upp av sig själva.",
            "Bjud in familjen under Administration → Användare.",
        ],
        "mobile": {"ios": "https://apps.apple.com/app/immich/id1613945652",
                   "android": "https://play.google.com/store/apps/details?id=app.alextran.immich"},
    },
    {
        "id": "passwords", "emoji": "🔑", "title": "Lösenord för familjen",
        "pitch": "En egen lösenordshanterare (Bitwarden-kompatibel) för hela familjen. Fyller i lösenord i mobilen och "
                 "webbläsaren, och delade lösenord för t.ex. Netflix och wifi.",
        "app": "casaos/Vaultwarden", "app_title": "Vaultwarden", "min_ram_gb": 0.5, "needs_https": True,
        "disk": "Mycket lite.",
        "steps": [
            "Öppna Vaultwarden och skapa ditt konto med ett långt huvudlösenord som du aldrig glömmer – det går inte att återställa.",
            "Installera Bitwarden-appen i mobilen och tillägget i webbläsaren. Välj \"Egen server\" och ange adressen nedan.",
            "Skapa en Organisation för familjen så kan ni dela lösenord.",
        ],
        "mobile": {"ios": "https://apps.apple.com/app/bitwarden-password-manager/id1137397744",
                   "android": "https://play.google.com/store/apps/details?id=com.x8bit.bitwarden"},
    },
    {
        "id": "movies", "emoji": "🎬", "title": "Filmer och serier",
        "pitch": "Din egen Netflix: titta på dina filmer, serier och din musik på TV:n, mobilen och datorn – med omslag, "
                 "beskrivningar och \"fortsätt titta\".",
        "app": "casaos/Jellyfin", "app_title": "Jellyfin", "min_ram_gb": 2, "disk": "Lika mycket som dina filmer tar.",
        "steps": [
            "Öppna Jellyfin och följ guiden: välj språk och skapa ditt konto.",
            "Lägg till ett bibliotek och peka på mappen /media (dina filmer i /DATA/Media).",
            "Ladda ner Jellyfin-appen till TV:n, mobilen eller surfplattan och ange adressen nedan.",
        ],
        "mobile": {"ios": "https://apps.apple.com/app/jellyfin-mobile/id1480192618",
                   "android": "https://play.google.com/store/apps/details?id=org.jellyfin.mobile"},
    },
    {
        "id": "cloud", "emoji": "☁️", "title": "Eget moln (som Dropbox)",
        "pitch": "Synka filer mellan datorer och mobiler, dela mappar, kalender och kontakter – utan att betala "
                 "för molnlagring och utan att någon annan läser dina filer.",
        "app": "casaos/Nextcloud", "app_title": "Nextcloud", "min_ram_gb": 2, "disk": "Lika mycket som du vill spara.",
        "steps": [
            "Öppna Nextcloud och skapa administratörskontot.",
            "Installera Nextcloud-appen på datorn och i mobilen och ange adressen nedan.",
            "Välj vilka mappar som ska synkas – och slå på automatisk uppladdning av foton om du vill.",
        ],
        "mobile": {"ios": "https://apps.apple.com/app/nextcloud/id1125420102",
                   "android": "https://play.google.com/store/apps/details?id=com.nextcloud.client"},
    },
    {
        "id": "notes", "emoji": "📝", "title": "Anteckningar och idéer",
        "pitch": "Skriv ner tankar, listor och idéer snabbt – från mobilen eller datorn. Taggar, sök och Markdown.",
        "app": "casaos/Memos", "app_title": "Memos", "min_ram_gb": 0.25, "disk": "Mycket lite.",
        "steps": [
            "Öppna Memos och skapa ditt konto.",
            "Skanna QR-koden nedan med mobilen och lägg sidan på hemskärmen – då känns den som en app.",
        ],
        "mobile": {},
    },
    {
        "id": "search", "emoji": "🔍", "title": "Sök utan spårning",
        "pitch": "Din egen sökmotor som frågar Google, Bing och DuckDuckGo åt dig – utan att någon bygger en profil på dig. "
                 "Gör den till standardsökmotor i webbläsaren.",
        "app": "wizzos/searxng", "app_title": "SearXNG", "min_ram_gb": 0.5, "disk": "Mycket lite.",
        "steps": [
            "Öppna sökmotorn och prova en sökning.",
            "Gör den till standardsökmotor: appens meny ⋯ → Gör till standardsökmotor.",
        ],
        "mobile": {},
    },
]
def all_goals() -> list[dict]:
    """Inbyggda mål + mål från WizzOS-paket (x-wizzos.goal)."""
    from . import recipes
    ids = {g["id"] for g in GOALS}
    return GOALS + [g for g in recipes.goals() if g["id"] not in ids]


def _by_id(gid: str) -> dict | None:
    return next((g for g in all_goals() if g["id"] == gid), None)


def _installed_for(g: dict, inst: list[dict]) -> dict | None:
    hits = [a for a in inst if a.get("store_key") == g["app"]]
    hits.sort(key=lambda a: (bool(a.get("trial")), a.get("status") != "running"))
    return hits[0] if hits else None


def checks(g: dict) -> list[dict]:
    """Klarar servern det här? (ok / warn / bad)"""
    out = []
    ram = psutil.virtual_memory().total / 1e9
    need = g.get("min_ram_gb", 0)
    if need:
        if ram + 0.2 >= need:
            out.append({"level": "ok", "text": f"Minne: {ram:.1f} GB – räcker (behöver ca {need:g} GB)."})
        elif ram * 1.5 >= need:
            out.append({"level": "warn", "text": f"Minne: {ram:.1f} GB – i minsta laget (behöver ca {need:g} GB). Prova först!"})
        else:
            out.append({"level": "bad", "text": f"Minne: {ram:.1f} GB – för lite (behöver ca {need:g} GB). Appen kommer att gå mycket trögt."})
    a = store.find(config.load()["stores"], g["app"])
    if not a:
        out.append({"level": "bad", "text": "Appen finns inte i dina appbutiker just nu."})
    elif a.get("architectures") and apps.arch() not in a["architectures"]:
        out.append({"level": "bad", "text": f"Appen finns inte för den här processorn ({apps.arch()})."})
    if g.get("needs_https"):
        if proxy.wizz_url():
            out.append({"level": "ok", "text": "Egen adress med https finns – krävs för att appen ska fungera i webbläsare och mobil."})
        else:
            out.append({"level": "warn", "text": "Appen kräver https. Ge WizzOS en egen adress (Inställningar → Egen adress) först."})
    if g.get("disk"):
        out.append({"level": "ok", "text": "Lagring: " + g["disk"]})
    return out


def listing() -> list[dict]:
    inst = apps.installed()["apps"]
    res = []
    for g in all_goals():
        a = _installed_for(g, inst)
        lv = [c["level"] for c in checks(g)]
        res.append({k: v for k, v in g.items()} | {
            "installed": bool(a), "app_name": a["name"] if a else "", "url": a.get("url", "") if a else "",
            "status": a.get("status", "") if a else "", "trial": bool(a and a.get("trial")),
            "fit": "bad" if "bad" in lv else "warn" if "warn" in lv else "ok"})
    return res


def detail(gid: str) -> dict:
    g = _by_id(gid)
    if not g:
        raise ValueError("Okänt mål.")
    item = next(x for x in listing() if x["id"] == gid)
    return item | {"checks": checks(g)}


def install(gid: str, trial_minutes: int = 0) -> dict:
    from . import trial
    g = _by_id(gid)
    if not g:
        raise ValueError("Okänt mål.")
    if trial_minutes and trial_minutes not in trial.CHOICES:
        raise ValueError("Ogiltig provtid.")
    if any(c["level"] == "bad" and ("processorn" in c["text"] or "finns inte i dina" in c["text"]) for c in checks(g)):
        raise ValueError("Appen går inte att installera här.")
    a = store.find(config.load()["stores"], g["app"])
    base = re.sub(r"[^a-z0-9-]+", "-", (a.get("name") or g["app_title"]).lower()).strip("-")[:28] or "app"
    name, n = base, 2
    while apps.app_dir(name).exists():
        name, n = f"{base}-{n}", n + 1
    return {"job": apps.install(a, name, {}, {}, trial_minutes), "app": name}
