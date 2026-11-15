"""Om jag försvinner.

Om du inte har använt WizzOS på en viss tid (standard 3 månader) och inte
svarar på tre påminnelser, får de du litar på ett brev från dig: din egen
text, en kort guide och länkar till de mappar du valt (foton, dokument …).

Du räknas som "här" när du:
  - använder WizzOS (inloggad), eller
  - trycker "Jag är här" i en påminnelse (e-post eller mobilnotis), eller
  - skannar QR-koden med mobilen och anger din trygghetskod.

Tidslinje efter tiden utan aktivitet:
  dag 0  påminnelse 1   dag 7  påminnelse 2   dag 14  sista påminnelse
  dag 21 brevet skickas – länkarna fungerar sedan i 90 dagar.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from datetime import datetime

from . import config

STATE = config.HOME / "legacy.json"
STEPS = [(0, "remind1"), (7, "remind2"), (14, "remind3"), (21, "release")]
DEFAULTS = {"enabled": False, "months": 3, "owner_name": "", "owner_email": "", "trustees": [], "letter": "",
            "practical": "", "folders": [], "share_days": 90, "base_url": "", "pin_hash": "", "pin_salt": ""}
_lock = threading.Lock()
_touched = 0.0


def cfg() -> dict:
    return {**DEFAULTS, **(config.load().get("legacy") or {})}


def _st() -> dict:
    try:
        return json.loads(STATE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _save_st(s: dict) -> None:
    s["log"] = s.get("log", [])[-60:]
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(STATE)


def state() -> dict:
    s = _st()
    changed = False
    if not s.get("last_alive"):
        s["last_alive"] = int(time.time()); changed = True
    if not s.get("qr_token"):
        s["qr_token"] = secrets.token_urlsafe(18); changed = True
    s.setdefault("done", [])
    if changed:
        _save_st(s)
    return s


def _log(s: dict, text: str) -> None:
    s.setdefault("log", []).append({"time": int(time.time()), "text": text})


def _pin(pin: str, salt: str) -> str:
    return hashlib.scrypt(pin.encode(), salt=salt.encode(), n=2**14, r=8, p=1, dklen=24).hex()


def base_url() -> str:
    from . import proxy
    return (cfg().get("base_url") or proxy.wizz_url() or "").rstrip("/")


def deadline(c: dict | None = None, s: dict | None = None) -> dict:
    c = c or cfg()
    s = s or state()
    start = s["last_alive"] + int(c["months"]) * 30 * 86400
    return {"remind1": start, "remind2": start + 7 * 86400, "remind3": start + 14 * 86400,
            "release": start + 21 * 86400}


def public() -> dict:
    c = cfg()
    s = state()
    d = deadline(c, s)
    out = {k: v for k, v in c.items() if k not in ("pin_hash", "pin_salt")}
    out.update(has_pin=bool(c["pin_hash"]), last_alive=s["last_alive"], last_source=s.get("last_source", ""),
               done=s["done"], released_at=s.get("released_at"), deadlines=d, log=s.get("log", [])[-15:][::-1],
               qr_url=f"{base_url()}/alive/{s['qr_token']}" if base_url() else "", effective_base=base_url())
    from . import mail
    out["mail_ok"] = mail.configured()
    return out


def save(ch: dict) -> dict:
    c = cfg()
    if ch.get("months") is not None:
        c["months"] = max(1, min(24, int(ch["months"])))
    for k in ("owner_name", "owner_email", "letter", "practical", "base_url"):
        if ch.get(k) is not None:
            c[k] = str(ch[k]).strip()[:20000]
    if c["base_url"] and not c["base_url"].startswith(("http://", "https://")):
        raise ValueError("Adressen ska börja med https://")
    if ch.get("trustees") is not None:
        ts = []
        for t in ch["trustees"][:6]:
            name, email = str(t.get("name", "")).strip(), str(t.get("email", "")).strip()
            if not name and not email:
                continue
            if "@" not in email:
                raise ValueError(f"E-postadressen för {name or 'en mottagare'} ser inte rätt ut.")
            ts.append({"name": name or email, "email": email})
        c["trustees"] = ts
    if ch.get("folders") is not None:
        from . import files
        fs = []
        for f in ch["folders"][:10]:
            f = str(f).strip()
            if f:
                p = files.resolve(f)
                if not p.exists():
                    raise ValueError(f"{f} finns inte.")
                fs.append(files.to_rel(p))
        c["folders"] = fs
    if ch.get("share_days") in (30, 90):
        c["share_days"] = ch["share_days"]
    pin = str(ch.get("pin") or "").strip()
    if pin:
        if not pin.isdigit() or not 4 <= len(pin) <= 8:
            raise ValueError("Trygghetskoden ska vara 4–8 siffror.")
        c["pin_salt"] = secrets.token_hex(8)
        c["pin_hash"] = _pin(pin, c["pin_salt"])
    if ch.get("enabled") is not None:
        c["enabled"] = bool(ch["enabled"])
    if c["enabled"]:
        from . import alerts, mail
        if not mail.configured():
            raise ValueError("Ställ in e-post först (Inställningar → E-post) – brevet skickas med e-post.")
        if not c["trustees"]:
            raise ValueError("Lägg till minst en person som ska få brevet.")
        if not c["owner_email"] and not alerts.cfg().get("enabled"):
            raise ValueError("Ange din egen e-post (eller slå på larm till mobilen) så att påminnelserna når dig.")
        if not c["letter"].strip():
            raise ValueError("Skriv ditt brev först.")
        if not c["pin_hash"]:
            raise ValueError("Välj en trygghetskod – den behövs när du checkar in med mobilen.")
    config.update(legacy=c)
    alive("inställningar")
    return public()


# ---------- här ----------

def alive(source: str) -> None:
    global _touched
    _touched = time.time()
    with _lock:
        s = state()
        was = s.get("done", [])
        s["last_alive"] = int(time.time())
        s["last_source"] = source
        if was:
            _log(s, f"Du checkade in ({source}). Nedräkningen började om."
                 + (" Breven var redan skickade." if "release" in was else ""))
            s["done"] = []
        _save_st(s)


def touch() -> None:
    """Anropas vid varje inloggad användning – skriver högst en gång i timmen."""
    if time.time() - _touched > 3600:
        try:
            alive("använde WizzOS")
        except Exception:  # noqa: BLE001
            pass


def token_kind(token: str) -> str:
    s = state()
    if hmac.compare_digest(token, s["qr_token"]):
        return "qr"
    if s.get("mail_token") and hmac.compare_digest(token, s["mail_token"]):
        return "mail"
    return ""


def check_in(token: str, pin: str, ip: str) -> str:
    kind = token_kind(token)
    if not kind:
        raise ValueError("Länken gäller inte längre.")
    if kind == "qr":
        from . import shares
        now = time.time()
        key = f"alive:{ip}"
        recent = [t for t in shares._fails.get(key, []) if now - t < 900]
        if len(recent) >= 5:
            raise ValueError("För många försök. Vänta en kvart.")
        c = cfg()
        if not c["pin_hash"] or not hmac.compare_digest(_pin(pin.strip(), c["pin_salt"]), c["pin_hash"]):
            shares._fails[key] = recent + [now]
            raise ValueError("Fel kod.")
    alive("mobilen (QR)" if kind == "qr" else "påminnelsen")
    return kind


# ---------- påminnelser och brevet ----------

def _remind(step: str, c: dict, s: dict, test: bool = False) -> None:
    from . import alerts, mail
    if not s.get("mail_token") or step == "remind1":
        s["mail_token"] = secrets.token_urlsafe(18)
    url = f"{base_url()}/alive/{s['mail_token']}" if base_url() else ""
    d = deadline(c, s)
    when = datetime.fromtimestamp(d["release"]).strftime("%-d/%-m")
    names = ", ".join(t["name"] for t in c["trustees"])
    n = {"remind1": "1 av 3", "remind2": "2 av 3", "remind3": "sista"}[step]
    text = (f"Hej{(' ' + c['owner_name']) if c['owner_name'] else ''}!\n\n"
            f"Du har inte använt WizzOS på {c['months']} månader. Är allt bra?\n\n"
            f"Om du inte svarar skickas ditt brev i \"Om jag försvinner\" till {names} den {when}.\n\n"
            + (f"Tryck här för att säga att du är här:\n{url}\n\n" if url else "Logga in i WizzOS så räknas du som här.\n\n")
            + "Du kan också bara logga in i WizzOS, eller skanna QR-koden och ange din trygghetskod.\n\n"
            f"(Påminnelse {n})")
    subject = f"{'[PROV] ' if test else ''}Är du där? Påminnelse {n} från WizzOS"
    sent = []
    if c["owner_email"] and mail.configured():
        try:
            mail.send(c["owner_email"], subject, text)
            sent.append("e-post")
        except Exception as e:  # noqa: BLE001
            _log(s, f"Påminnelsen kunde inte mejlas: {e}")
    try:
        if alerts.cfg().get("ntfy_topic") or alerts.cfg().get("webhook_url"):
            alerts.send("Är du där?", f"Du har inte använt WizzOS på länge. Svara före {when}, annars skickas ditt brev "
                        f"till {names}. {url}", 5 if step == "remind3" else 4, "wave")
            sent.append("mobilen")
    except Exception:  # noqa: BLE001
        pass
    _log(s, f"{'Provpåminnelse' if test else 'Påminnelse ' + n} skickad ({', '.join(sent) or 'kunde inte skickas'}).")


def letter_text(c: dict, links: list[tuple[str, str]], until: int, test: bool = False) -> str:
    from . import alerts
    owner = c["owner_name"] or "Ägaren"
    parts = []
    if test:
        parts.append("[PROV – så här ser brevet ut. Länkarna nedan är bara exempel.]\n")
    parts.append(c["letter"].strip())
    parts.append("\n\n———\n")
    if links:
        parts.append(f"{owner} ville att du skulle få det här:\n")
        for name, url in links:
            parts.append(f"  • {name}\n    {url}")
        parts.append(f"\nLänkarna fungerar till {datetime.fromtimestamp(until).strftime('%-d/%-m %Y')}. "
                     "Öppna dem och ladda ner det du vill spara – på en dator går det att ta allt som en zip-fil.\n")
    if c["practical"].strip():
        parts.append("\nPraktiskt:\n" + c["practical"].strip() + "\n")
    parts.append(f"\nOm servern: den heter \"{alerts._server_name()}\" och kör WizzOS – ett program som håller ordning "
                 "på appar, filer och backup. Den fortsätter fungera av sig själv ett tag, men behöver ström och "
                 "internet. Be någon som kan datorer om hjälp om du vill behålla den.\n")
    parts.append(f"\nVarför du får det här: {owner} har inte använt servern på {c['months']} månader och har inte "
                 "svarat på tre påminnelser. Då skickas det här brevet automatiskt, som "
                 f"{owner} själv bestämde. Om allt ändå är bra – hör av dig till {owner}.")
    return "\n".join(parts)


def release(c: dict, s: dict) -> None:
    from . import mail, shares
    links = []
    until = int(time.time()) + int(c["share_days"]) * 86400
    for f in c["folders"]:
        try:
            r = shares.create(f, c["share_days"], note=f"Från {c['owner_name'] or 'ägaren'}", by="Om jag försvinner")
            links.append((r["name"], f"{base_url()}{r['url_path']}"))
        except Exception as e:  # noqa: BLE001
            _log(s, f"Kunde inte dela {f}: {e}")
    text = letter_text(c, links, until)
    ok = []
    for t in c["trustees"]:
        try:
            mail.send(t["email"], f"Ett brev från {c['owner_name'] or 'en vän'}", f"Hej {t['name']}!\n\n" + text,
                      reply_to=c["owner_email"])
            ok.append(t["name"])
        except Exception as e:  # noqa: BLE001
            _log(s, f"Brevet till {t['name']} kunde inte skickas: {e}")
    s["released_at"] = int(time.time())
    _log(s, f"Brevet skickades till {', '.join(ok) or 'ingen (fel)'} med {len(links)} länk(ar).")
    if c["owner_email"]:
        try:
            mail.send(c["owner_email"], "Ditt brev i \"Om jag försvinner\" har skickats",
                      f"Brevet gick till {', '.join(ok)}. Om du är okej: logga in i WizzOS och hör av dig till dem. "
                      "Delningarna kan du stoppa under Trygghet.")
        except Exception:  # noqa: BLE001
            pass


def send_test(what: str) -> str:
    from . import mail
    c = cfg()
    if not mail.configured():
        raise ValueError("Ställ in e-post först.")
    if not c["owner_email"]:
        raise ValueError("Ange din egen e-post – provet skickas bara till dig.")
    with _lock:
        s = state()
        if what == "reminder":
            _remind("remind1", c, s, test=True)
        else:
            links = [(f.rstrip('/').split('/')[-1] or f, f"{base_url() or 'https://din-server'}/s/exempel") for f in c["folders"]]
            mail.send(c["owner_email"], f"[PROV] Ett brev från {c['owner_name'] or 'en vän'}",
                      "Hej (namnet på mottagaren)!\n\n" + letter_text(c, links, int(time.time()) + c["share_days"] * 86400, test=True))
            _log(s, "Provbrevet skickades till dig.")
        _save_st(s)
    return c["owner_email"]


def tell_trustees() -> list[str]:
    from . import mail
    c = cfg()
    if not mail.configured() or not c["trustees"]:
        raise ValueError("Ställ in e-post och lägg till mottagare först.")
    owner = c["owner_name"] or "En vän"
    sent = []
    for t in c["trustees"]:
        mail.send(t["email"], f"{owner} har valt dig som betrodd kontakt",
                  f"Hej {t['name']}!\n\n{owner} har en egen server hemma (WizzOS) och har valt dig som en av dem som "
                  "ska få ett brev och tillgång till viktiga filer – om något skulle hända och servern inte används på länge.\n\n"
                  "Du behöver inte göra något nu. Spara gärna det här mejlet så att du känner igen avsändaren.",
                  reply_to=c["owner_email"])
        sent.append(t["name"])
    with _lock:
        s = state()
        _log(s, f"Du berättade för {', '.join(sent)} att de är valda.")
        _save_st(s)
    return sent


def tick() -> None:
    c = cfg()
    if not c["enabled"]:
        return
    with _lock:
        s = state()
        d = deadline(c, s)
        now = time.time()
        for _, step in STEPS:
            if step in s["done"] or now < d[step]:
                continue
            if step == "release":
                release(c, s)
            else:
                _remind(step, c, s)
            s["done"].append(step)
            _save_st(s)
            break  # högst ett steg per varv


_started = False


def start_watch() -> None:
    global _started
    if _started:
        return
    _started = True

    def loop():
        time.sleep(90)
        while True:
            try:
                tick()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(900)

    threading.Thread(target=loop, daemon=True, name="legacy").start()


def alive_page(token: str, kind: str, msg: str = "", ok: bool = False) -> str:
    from html import escape
    from .shares import _page
    if ok:
        return _page("Tack!", "<div class=card><h1>✅ Tack – du är incheckad</h1>"
                     f"<p class=m>Nedräkningen har börjat om. Nästa gång märks det först om {cfg()['months']} månader.</p></div>")
    if not kind:
        return _page("Länken gäller inte", "<div class=card><h1>Länken gäller inte längre</h1>"
                     "<p class=m>Logga in i WizzOS i stället – då räknas du som här.</p></div>")
    pin = ("<p class=m>Ange din trygghetskod.</p><input name=pin inputmode=numeric autocomplete=off maxlength=8 autofocus aria-label=Kod>"
           if kind == "qr" else "<p class=m>Tryck på knappen så vet WizzOS att allt är bra.</p>")
    return _page("Jag är här", f"""<div class=card><h1>👋 Jag är här</h1><form method=post action='/alive/{escape(token)}'>{pin}
      {f"<p class=err>{escape(msg)}</p>" if msg else ""}<button class=btn type=submit>Jag är här</button></form></div>""")
