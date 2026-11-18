"""AI-hjälp på svenska.

Assistenten får en sammanfattning av servern (status, appar, backup, loggar för
appen det gäller, träffar i appbutiken) och svarar på enkel svenska. Den kan
föreslå åtgärder med taggar som [[starta-om:2fauth]] – gränssnittet gör dem till
knappar, och inget händer förrän användaren klickar.

Fungerar med alla OpenAI-kompatibla tjänster (LiteLLM, Ollama, OpenAI ...).
Hemligheter (lösenord, nycklar) maskeras innan något skickas iväg.
"""
from __future__ import annotations

import re

import httpx

from . import apps, backup, config, proxy, snapshots, store, system

DEFAULTS = {"base_url": "", "api_key": "", "model": "gratis-chatt", "speech_model": "gratis-tal",
            "cf_id": "", "cf_secret": "", "enabled": False}
SECRET_KEYS = ("api_key", "cf_secret")

SYSTEM_PROMPT = """Du är WizzOS-assistenten, inbyggd i en privat hemserver som heter WizzOS.
Ägaren är nybörjare. Svara alltid på enkel, vardaglig svenska med korrekt grammatik.
Var kort och konkret (högst ca 150 ord om inte mer behövs). Förklara facktermer med vanliga ord.

Du får aktuell information om servern längre ner. Bygg svaret på den. Hitta inte på appar,
siffror eller fel som inte finns i informationen. Vet du inte, säg det.

Du kan föreslå åtgärder. Skriv dem som taggar på egen rad, exakt så här (gränssnittet gör dem
till knappar som användaren själv trycker på):
[[installera:<butiksnyckel>]]   – bara nycklar som finns i listan "Träffar i appbutiken"
[[öppna:<appnamn>]]  [[starta:<appnamn>]]  [[stoppa:<appnamn>]]  [[starta-om:<appnamn>]]
[[uppdatera:<appnamn>]]  [[logg:<appnamn>]]  [[ångra:<appnamn>]]  [[backup:<appnamn>]]
<appnamn> är det tekniska namnet i listan över installerade appar (t.ex. 2fauth).
Föreslå bara åtgärder som faktiskt hjälper. Säg aldrig att du själv har gjort något.

WizzOS-funktioner du kan hänvisa till: Ångra-knappen (återställningspunkter före varje ändring,
"Nyligen borttagna" i 30 dagar), säkra uppdateringar som backar själva om appen kraschar,
egna https-adresser per app, nattlig backup med återställningstest, Byt adress i appmenyn."""

# Svenska ord → engelska sökord för appbutiken (butikerna är på engelska).
SYNONYMS = {
    "film": "movie media video", "filmer": "movie media video", "serie": "tv media", "serier": "tv media",
    "tv": "tv media iptv", "musik": "music audio", "ljud": "audio music", "foto": "photo image",
    "foton": "photo image gallery", "bild": "photo image", "bilder": "photo image gallery",
    "lösenord": "password vault", "lösen": "password", "filer": "file files cloud storage",
    "fil": "file", "moln": "cloud", "dokument": "document paperless pdf", "kvitto": "document receipt",
    "anteckning": "notes note", "anteckningar": "notes note wiki", "bok": "book ebook",
    "böcker": "book ebook library", "ljudbok": "audiobook", "ljudböcker": "audiobook podcast",
    "podd": "podcast", "spel": "game gaming", "hem": "home automation", "smarta": "home automation",
    "ladda": "download", "nedladdning": "download torrent", "torrent": "torrent download",
    "övervaka": "monitor uptime", "övervakning": "monitor uptime status", "recept": "recipe",
    "ekonomi": "finance budget", "budget": "budget finance", "chatt": "chat", "ai": "ai llm chat",
    "kod": "code developer git", "säkerhet": "security", "reklam": "ad block dns",
    "annonser": "ad block dns", "vpn": "vpn wireguard", "mejl": "mail email", "e-post": "mail email",
    "kalender": "calendar", "kontakter": "contacts", "dashboard": "dashboard", "startsida": "dashboard homepage",
    "länkar": "bookmark links", "bokmärken": "bookmark", "2fa": "2fa otp authenticator",
}
STOPWORDS = set("jag du vi det den att och som för med till från på av är har vill kan ska hur "
                "vad varför när var vilken vilka finns någon något en ett min mitt mina mig "
                "inte bara också eller men om så the and för appen app appar".split())


def cfg() -> dict:
    return {**DEFAULTS, **(config.load().get("ai") or {})}


def public_cfg() -> dict:
    c = cfg()
    out = {k: v for k, v in c.items() if k not in SECRET_KEYS}
    out["has_key"] = bool(c.get("api_key"))
    out["has_cf"] = bool(c.get("cf_id") and c.get("cf_secret"))
    out["configured"] = bool(c.get("enabled") and c.get("base_url") and c.get("model"))
    return out


def save(changes: dict) -> dict:
    c = cfg()
    for k in ("base_url", "model", "speech_model", "cf_id"):
        if changes.get(k) is not None:
            c[k] = str(changes[k]).strip()
    for k in SECRET_KEYS:
        if changes.get(k):  # tomt = behåll det som är sparat
            c[k] = str(changes[k]).strip()
    if changes.get("clear_cf"):
        c["cf_id"], c["cf_secret"] = "", ""
    if not re.match(r"^https?://", c["base_url"]):
        raise ValueError("Adressen måste börja med http:// eller https://")
    c["enabled"] = bool(changes.get("enabled", True))
    config.update(ai=c)
    return public_cfg()


# ---------- maskning ----------

SECRET_NAME = re.compile(r"(pass|pwd|secret|token|key|auth|cred|salt|private)", re.I)
SECRET_INLINE = re.compile(
    r"((?:password|passwd|pwd|secret|token|api[_-]?key|auth)[\"']?\s*[=:]\s*[\"']?)([^\s\"',;]+)", re.I)
LONG_TOKEN = re.compile(r"\b[A-Za-z0-9+/_\-]{40,}={0,2}\b")


def scrub(text: str) -> str:
    text = SECRET_INLINE.sub(lambda m: m.group(1) + "•••", text)
    return LONG_TOKEN.sub("•••", text)


def scrub_compose(text: str) -> str:
    out = []
    for line in text.splitlines():
        m = re.match(r"^(\s*-?\s*)([A-Za-z0-9_]+)(\s*[:=]\s*)(.+)$", line)
        if m and SECRET_NAME.search(m.group(2)) and m.group(4).strip() not in ("", "''", '""'):
            line = f"{m.group(1)}{m.group(2)}{m.group(3)}•••"
        out.append(line)
    return scrub("\n".join(out))


# ---------- sammanhang ----------

def _fmt_bytes(b) -> str:
    b = float(b or 0)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.0f} {u}"
        b /= 1024
    return f"{b:.1f} PB"


def _words(text: str) -> list[str]:
    words = re.findall(r"[a-zåäö0-9][a-zåäö0-9-]{1,}", text.lower())
    out = []
    for w in words:
        if w in STOPWORDS:
            continue
        out.append(w)
        if w in SYNONYMS:
            out.extend(SYNONYMS[w].split())
    return out


def _store_hits(question: str, limit: int = 10) -> list[dict]:
    words = [w for w in _words(question) if len(w) >= 3]
    if not words:
        return []
    scored = []
    for a in store.all_apps(config.load()["stores"]):
        title = a["title"].lower()
        hay = f"{title} {a['tagline']} {a['category']} {a['description'][:400]}".lower()
        score = sum((5 if w in title else 0) + (2 if w in a["category"].lower() else 0)
                    + (1 if w in hay else 0) for w in words)
        if score:
            scored.append((score, a))
    scored.sort(key=lambda x: -x[0])
    return [a for _, a in scored[:limit]]


def _mentioned_apps(text: str, installed: list[dict]) -> list[str]:
    low = text.lower()
    out = []
    for a in installed:
        names = {a["name"], a["title"].lower(), (a.get("address") or "")}
        if any(n and len(n) >= 3 and n.lower() in low for n in names):
            out.append(a["name"])
    return out


def build_context(question: str, history_text: str, app: str | None, extra: str | None) -> str:
    parts = []
    s = system.stats()
    parts.append(
        f"## Servern\nNamn: {s['hostname']} · {s['os']} · drifttid {s['uptime'] // 3600} h\n"
        f"Processor {s['cpu']}% ({s['cores']} kärnor, last {s['load'][0]}) · "
        f"minne {_fmt_bytes(s['mem']['used'])} av {_fmt_bytes(s['mem']['total'])} ({s['mem']['percent']}%) · "
        f"lagring {_fmt_bytes(s['disk']['used'])} av {_fmt_bytes(s['disk']['total'])} ({s['disk']['percent']}%)"
        + (f" · temp {s['temp']} °C" if s.get("temp") else ""))
    ok, ver = apps.docker_ok()
    if not ok:
        parts.append(f"OBS: Docker svarar inte: {ver}")

    inst = apps.installed()
    bst = backup.state().get("apps", {})
    lines = []
    for a in inst["apps"]:
        b = bst.get(a["name"], {})
        bs = ("backup ok" if b.get("backup_ok") else "backup MISSLYCKADES" if b.get("last_backup") else "ingen backup")
        ts = ("återställning testad ok" if b.get("test_ok") else "återställningstest MISSLYCKADES"
              if b.get("last_test") else "ej testad")
        lines.append(f"- {a['name']} ({a['title']}): {a['status']}, version {a.get('version') or '?'}, "
                     f"adress {a.get('url') or ('port ' + str(a.get('port')) if a.get('port') else 'ingen')}, "
                     f"{len(snapshots.list_for(a['name']))} ångra-punkter, {bs}, {ts}")
    parts.append("## Installerade appar\n" + ("\n".join(lines) or "(inga)"))
    if inst["others"]:
        parts.append("## Andra containrar (inte installerade via WizzOS)\n" + "\n".join(
            f"- {o['name']}: {o['state']} ({o['image']})" for o in inst["others"][:15]))
    removed = snapshots.removed_apps()
    if removed:
        parts.append("## Nyligen borttagna (går att få tillbaka)\n" + "\n".join(
            f"- {r['name']} ({r['title']})" for r in removed))

    bs = backup.summary()
    if bs["configured"]:
        lr = bs.get("last_run")
        parts.append(f"## Backup\nMål: {bs['target']}, varje natt kl {bs['time']}. "
                     + (f"Senaste körning: {'ok' if lr['ok'] else 'FEL: ' + ', '.join(lr['failed'])}. "
                        if lr else "Ingen körning än. ")
                     + (f"Använt {_fmt_bytes(bs['repo_size'])}" if bs.get("repo_size") is not None else "")
                     + (f" av taket {bs['max_gb']:g} GB." if bs.get("max_gb") else ""))
    else:
        parts.append("## Backup\nInte inställd än (görs under menyn Backup).")
    px = proxy.settings()
    parts.append("## Egen adress\n" + (f"På: appar nås på https://<app>.{px['domain']}" if px["enabled"]
                                       else "Av: appar nås via IP och port."))

    # Loggar och inställningar för appar som frågan gäller
    focus = []
    if app:
        focus.append(app)
    focus += [n for n in _mentioned_apps(question + " " + history_text, inst["apps"]) if n not in focus]
    for n in focus[:2]:
        try:
            logs = apps.logs(n)
        except Exception as e:  # noqa: BLE001
            logs = f"(kunde inte läsa loggen: {e})"
        tail = "\n".join(logs.strip().splitlines()[-120:])[-7000:]
        try:
            comp = scrub_compose(apps.read_compose(n))[:3500]
        except Exception:  # noqa: BLE001
            comp = ""
        parts.append(f"## Logg för {n} (senaste raderna)\n```\n{scrub(tail)}\n```")
        if comp:
            parts.append(f"## Inställningar (compose) för {n}\n```yaml\n{comp}\n```")

    if extra:
        parts.append(f"## Text (jobblogg eller fil) som användaren frågar om\n```\n{scrub(extra[-6000:])}\n```")

    hits = _store_hits(question)
    if hits:
        parts.append("## Träffar i appbutiken (butiksnyckel – titel: beskrivning)\n" + "\n".join(
            f"- {a['key']} – {a['title']} [{a['category']}]: {a['tagline'][:140]}" for a in hits))
    return "\n\n".join(parts)


# ---------- anrop ----------

def _client(c: dict, json_body: bool = True) -> httpx.Client:
    headers = {"Content-Type": "application/json"} if json_body else {}
    if c.get("api_key"):
        headers["Authorization"] = f"Bearer {c['api_key']}"
    if c.get("cf_id") and c.get("cf_secret"):
        headers["CF-Access-Client-Id"] = c["cf_id"]
        headers["CF-Access-Client-Secret"] = c["cf_secret"]
    return httpx.Client(base_url=c["base_url"].rstrip("/"), headers=headers, timeout=120,
                        follow_redirects=False)


def _check(r: httpx.Response, c: dict) -> None:
    if r.status_code in (301, 302, 303) or "cloudflareaccess" in r.headers.get("location", ""):
        if not (c.get("cf_id") and c.get("cf_secret")):
            raise RuntimeError("Adressen skyddas av Cloudflare Access, men ingen servicenyckel är sparad. "
                               "Fyll i Client ID och Client Secret under \"Cloudflare Access-servicenyckel\".")
        raise RuntimeError("Cloudflare Access godtog inte servicenyckeln. Kontrollera Client ID "
                           "(slutar på .access) och Client Secret.")
    if r.status_code == 401:
        raise RuntimeError("AI-tjänsten godtog inte nyckeln.")
    if r.status_code == 429:
        raise RuntimeError("AI-tjänsten har nått sin gräns för stunden. Försök igen om en liten stund.")
    if r.status_code >= 400:
        raise RuntimeError(f"AI-tjänsten svarade med fel {r.status_code}: {r.text[:200]}")


def complete(messages: list[dict], c: dict | None = None, max_tokens: int = 1200) -> str:
    c = c or cfg()
    with _client(c) as cl:
        r = cl.post("/chat/completions", json={"model": c["model"], "messages": messages,
                                               "temperature": 0.3, "max_tokens": max_tokens})
    _check(r, c)
    try:
        msg = r.json()["choices"][0]["message"]
    except (ValueError, KeyError, IndexError):
        raise RuntimeError("AI-tjänsten gav ett svar som inte gick att läsa.")
    text = (msg.get("content") or "").strip()
    if not text:
        raise RuntimeError("Modellen svarade tomt. Försök igen.")
    # Vissa modeller skriver ut sitt "tänkande" – ta bort det.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    return text


GHOST_PHRASES = {"tack för att du tittade", "tack för att ni tittade", "tack för att du lyssnade",
                 "thank you for watching", "thanks for watching", "you", "tack", "musik"}


def transcribe(audio: bytes, filename: str = "voice.ogg", c: dict | None = None) -> str:
    """Tal till text (t.ex. ett röstmeddelande) via AI-tjänstens talmodell, standard "gratis-tal"."""
    from . import i18n
    c = c or cfg()
    if not public_cfg()["configured"]:
        raise ValueError("AI-hjälpen är inte inställd än (Inställningar → AI-hjälp).")
    model = c.get("speech_model") or DEFAULTS["speech_model"]
    with _client(c, json_body=False) as cl:
        r = cl.post("/audio/transcriptions", files={"file": (filename, audio, "audio/ogg")},
                    data={"model": model, "language": i18n.server_lang()})
    if r.status_code in (400, 404) and model in r.text:
        raise RuntimeError(f"AI-tjänsten har ingen talmodell som heter {model}.")
    _check(r, c)
    try:
        text = (r.json().get("text") or "").strip()
    except ValueError:
        raise RuntimeError("Talmodellen gav ett svar som inte gick att läsa.")
    # Whisper "hör" ibland textremsor från tv när ljudet är tyst – räkna det som tomt.
    ghost = re.sub(r"[\W_]+", " ", text.lower()).strip()
    if not text or ghost in GHOST_PHRASES or ghost.startswith(("textning", "undertext", "svensktextning")):
        raise RuntimeError("Jag hörde inget i ljudet.")
    return text


def test(c: dict | None = None) -> str:
    return complete([{"role": "user", "content": "Svara bara med ordet: Hej"}], c, max_tokens=200)


def chat(messages: list[dict], app: str | None = None, extra: str | None = None) -> dict:
    c = cfg()
    if not public_cfg()["configured"]:
        raise ValueError("AI-hjälpen är inte inställd än (Inställningar → AI-hjälp).")
    msgs = [m for m in messages if m.get("role") in ("user", "assistant") and m.get("content")][-10:]
    if not msgs or msgs[-1]["role"] != "user":
        raise ValueError("Skriv en fråga.")
    for m in msgs:
        m["content"] = str(m["content"])[:4000]
    if app:
        apps.app_dir(app)  # kontrollera namnet
    history = " ".join(m["content"] for m in msgs[:-1])
    ctx = build_context(msgs[-1]["content"], history, app, extra)
    from . import i18n
    prompt = SYSTEM_PROMPT if i18n.current() == "sv" else SYSTEM_PROMPT + (
        "\n\nIMPORTANT: The owner uses WizzOS in English. Always answer in simple, everyday English "
        "(the server information below is in Swedish – translate as needed). Keep the action tags exactly as specified.")
    full = [{"role": "system", "content": prompt + "\n\n# Aktuell information\n\n" + ctx}, *msgs]
    reply = complete(full, c)
    return {"reply": reply, "model": c["model"]}
