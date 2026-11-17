"""Prata med din server via Telegram.

Du skapar en egen bot hos @BotFather (gratis, två minuter) och klistrar in dess nyckel i WizzOS.
Sedan kopplar du din Telegram med en engångskod. Bara kopplade chattar får svar – alla andra ignoreras.

I chatten:
  /status   hur servern mår          /appar   alla appar
  /backup   senaste backupen         /hjalp   vad boten kan
  allt annat → AI-hjälpen svarar (med samma underlag som ✨ Fråga i WizzOS), och föreslagna
  åtgärder blir knappar (starta om, uppdatera, visa logg …). Stoppa kräver ett extra ja.
Larm och agentens frågor kommer också hit – godkänn/neka med knappar direkt i chatten.
"""
from __future__ import annotations

import re
import secrets
import threading
import time

import httpx

from . import apps, config

API = "https://api.telegram.org"
_history: dict[int, list[dict]] = {}
_offset = 0
_started = False
_TAG = re.compile(r"\[\[([a-zåäö-]+):([^\]\s]+)\]\]")
VERBS = {"starta": "▶️ Starta", "stoppa": "⏹ Stoppa", "starta-om": "🔄 Starta om", "uppdatera": "⬆️ Uppdatera",
         "logg": "📜 Logg", "backup": "🛟 Backup", "öppna": "🔗 Öppna"}


def cfg() -> dict:
    return {"token": "", "chats": [], "alerts": True, "bot": "", **(config.load().get("telegram") or {})}


def _save(**ch) -> dict:
    c = {**cfg(), **ch}
    config.update(telegram=c)
    return c


def public() -> dict:
    c = cfg()
    pair = c.get("pair") or {}
    return {"configured": bool(c["token"]), "bot": c.get("bot", ""), "alerts": c["alerts"],
            "chats": [{"id": x["id"], "name": x.get("name", "")} for x in c["chats"]],
            "pair_code": pair.get("code") if pair.get("until", 0) > time.time() else None}


def _call(method: str, token: str | None = None, http_timeout: float = 20, **params) -> dict:
    t = token or cfg()["token"]
    r = httpx.post(f"{API}/bot{t}/{method}", json=params, timeout=http_timeout)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description") or f"Telegram svarade {r.status_code}")
    return data["result"]


def set_token(token: str) -> dict:
    token = (token or "").strip()
    if not re.match(r"^\d{5,}:[A-Za-z0-9_-]{30,}$", token):
        raise ValueError("Det där ser inte ut som en bot-nyckel från @BotFather (siffror:bokstäver).")
    try:
        me = _call("getMe", token)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Telegram godkände inte nyckeln: {e}")
    _save(token=token, bot=me.get("username", ""), chats=[], pair=None)
    start()
    return public()


def start_pairing() -> dict:
    if not cfg()["token"]:
        raise ValueError("Klistra in bot-nyckeln först.")
    code = f"{secrets.randbelow(900000) + 100000}"
    _save(pair={"code": code, "until": time.time() + 600})
    return public()


def unlink(chat_id: int | None = None) -> dict:
    if chat_id is None:
        _save(token="", bot="", chats=[], pair=None)
    else:
        _save(chats=[x for x in cfg()["chats"] if x["id"] != chat_id])
    return public()


def set_alerts(on: bool) -> dict:
    _save(alerts=bool(on))
    return public()


# ---------- skicka ----------

def send(chat_id: int, text: str, buttons: list[list[dict]] | None = None) -> None:
    from . import i18n
    params = {"chat_id": chat_id, "text": i18n.tr(text, i18n.server_lang())[:4000], "disable_web_page_preview": True}
    if buttons:
        params["reply_markup"] = {"inline_keyboard": buttons}
    _call("sendMessage", **params)


def broadcast(text: str, buttons: list[list[dict]] | None = None) -> bool:
    """Till alla kopplade chattar. True om minst en fick det."""
    c = cfg()
    if not c["token"] or not c["chats"]:
        return False
    ok = False
    for x in c["chats"]:
        try:
            send(x["id"], text, buttons)
            ok = True
        except Exception:  # noqa: BLE001
            pass
    return ok


def alert(title: str, message: str) -> bool:
    c = cfg()
    return bool(c["alerts"]) and broadcast(f"🔔 {title}\n\n{message}")


def approval(r: dict) -> None:
    """Agentens/AI:ns fråga med Godkänn/Neka direkt i chatten."""
    broadcast(f"🤖 {r['who']} vill: {r['title']}\n\n{r['detail']}\n\nSvara inom 15 minuter – annars händer ingenting.",
              [[{"text": "✅ Godkänn", "callback_data": f"ap:{r['id']}:y"},
                {"text": "⛔ Neka", "callback_data": f"ap:{r['id']}:n"}]])


# ---------- ta emot ----------

def _paired(chat_id: int) -> bool:
    return any(x["id"] == chat_id for x in cfg()["chats"])


def _status_text() -> str:
    from . import backup, system
    st = system.stats()
    inst = [a for a in apps.installed()["apps"] if not a.get("trial")]
    down = [a["title"] for a in inst if a.get("status") != "running" and not a.get("user_stopped")]
    sleeping = [a["title"] for a in inst if a.get("asleep")]
    b = backup.summary()
    run = b.get("last_run") or {}
    lines = [f"🖥️ {config.load().get('server_name') or system.hostname()}",
             f"Processor {round(__import__('psutil').cpu_percent(0.5))} % · minne {round(st['mem']['percent'])} %"
             + (f" · disk {round(st['disk']['percent'])} %" if isinstance(st.get("disk"), dict) and "percent" in st["disk"] else ""),
             f"Appar: {len(inst) - len(down)} av {len(inst)} igång" + (f" · 💤 {len(sleeping)} sover" if sleeping else "")]
    if down:
        lines.append("⚠️ Inte igång: " + ", ".join(down))
    if run:
        lines.append(f"🛟 Backup: {'OK' if run.get('ok') else 'MED FEL'} {time.strftime('%Y-%m-%d %H:%M', time.localtime(run['time']))}")
    elif b.get("configured"):
        lines.append("🛟 Backup: ingen körning än")
    return "\n".join(lines)


def _apps_text() -> str:
    icon = {"running": "🟢", "stopped": "⚪", "partial": "🟡", "restarting": "🟠"}
    rows = []
    for a in apps.installed()["apps"]:
        st = "💤" if a.get("asleep") else icon.get(a.get("status"), "🔴")
        rows.append(f"{st} {a['title']} ({a['name']})")
    return "\n".join(rows) or "Inga appar installerade."


def _buttons_for(reply: str) -> tuple[str, list[list[dict]]]:
    names = {a["name"] for a in apps.installed()["apps"]}
    rows, seen = [], set()
    for verb, target in _TAG.findall(reply):
        if verb in VERBS and target in names and (verb, target) not in seen and len(rows) < 6:
            seen.add((verb, target))
            rows.append([{"text": f"{VERBS[verb]} {target}", "callback_data": f"a:{verb}:{target}"[:64]}])
    text = _TAG.sub("", reply).strip()
    return text, rows


def _ask_ai(chat_id: int, text: str) -> None:
    from . import ai
    hist = _history.setdefault(chat_id, [])
    hist.append({"role": "user", "content": text[:2000]})
    try:
        _call("sendChatAction", chat_id=chat_id, action="typing")
        try:
            reply = ai.chat(list(hist[-8:]))["reply"]
        except RuntimeError:  # gratismodellen svarar ibland tomt – ett försök till
            _call("sendChatAction", chat_id=chat_id, action="typing")
            reply = ai.chat(list(hist[-8:]))["reply"]
    except Exception as e:  # noqa: BLE001
        hist.pop()
        send(chat_id, f"AI-hjälpen svarar inte just nu: {e}\n\nSkriv /status eller /appar så svarar jag ändå.")
        return
    hist.append({"role": "assistant", "content": reply})
    del hist[:-12]
    body, rows = _buttons_for(reply)
    send(chat_id, body or "…", rows or None)


def _do(chat_id: int, verb: str, name: str) -> None:
    from . import backup, jobs, proxy
    a = next((x for x in apps.installed()["apps"] if x["name"] == name), None)
    if not a:
        send(chat_id, "Appen finns inte längre.")
        return
    t = a["title"]
    if verb == "stoppa":
        send(chat_id, f"Vill du verkligen stoppa {t}?",
             [[{"text": f"⏹ Ja, stoppa {t}", "callback_data": f"a:stoppa!:{name}"[:64]}]])
        return
    if verb in ("starta", "starta-om", "stoppa!"):
        what = {"starta": "start", "starta-om": "restart", "stoppa!": "stop"}[verb]
        apps.action(name, what)
        send(chat_id, f"✓ {t}: {dict(start='startad', restart='omstartad', stop='stoppad')[what]}.")
    elif verb == "uppdatera":
        apps.update(name)
        send(chat_id, f"⬆️ Uppdaterar {t} säkert (backar själv om den kraschar). Du får ett meddelande om något går fel.")
    elif verb == "backup":
        if not backup.cfg().get("type"):
            send(chat_id, "Backup är inte inställd än.")
            return
        jobs.start(f"Backup av {t}", lambda log: backup._with_app_lock(name, lambda: backup.backup_app(name, log)))
        send(chat_id, f"🛟 Säkerhetskopierar {t} …")
    elif verb == "logg":
        from . import ai
        send(chat_id, f"📜 {t} – senaste raderna:\n\n" + ai.scrub(apps.logs(name))[-3000:])
    elif verb == "öppna":
        url = a.get("url") or ""
        try:
            url = url or proxy.app_url(a)
        except Exception:  # noqa: BLE001
            pass
        send(chat_id, url or "Appen har ingen egen adress – öppna den från WizzOS.")


def _handle(upd: dict) -> None:
    from . import approvals
    if "callback_query" in upd:
        q = upd["callback_query"]
        chat_id = q.get("message", {}).get("chat", {}).get("id")
        try:
            _call("answerCallbackQuery", callback_query_id=q["id"])
        except Exception:  # noqa: BLE001
            pass
        if not chat_id or not _paired(chat_id):
            return
        data = q.get("data", "")
        try:
            if data.startswith("ap:"):
                _, aid, yn = data.split(":", 2)
                r = approvals.decide(aid, yn == "y", "telegram")
                send(chat_id, ("✅ Godkänt – det görs nu." if yn == "y" else "⛔ Nekat – inget händer.")
                     if r else "Frågan har redan besvarats eller gått ut.")
            elif data.startswith("a:"):
                _, verb, name = data.split(":", 2)
                _do(chat_id, verb, name)
        except Exception as e:  # noqa: BLE001
            send(chat_id, f"Det gick inte: {e}")
        return
    msg = upd.get("message") or {}
    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()
    if not chat_id or not text:
        return
    if not _paired(chat_id):
        pair = cfg().get("pair") or {}
        if pair.get("until", 0) > time.time() and secrets.compare_digest(text, pair.get("code", "")):
            who = msg.get("from", {})
            name = " ".join(x for x in (who.get("first_name"), who.get("last_name")) if x) or who.get("username", "")
            _save(chats=[*cfg()["chats"], {"id": chat_id, "name": name[:40]}], pair=None)
            send(chat_id, "✅ Kopplad! Nu kan du prata med din server här. Skriv /hjalp för att se vad jag kan.")
        return  # okända chattar får inget svar alls
    cmd = text.split()[0].split("@")[0].lower()
    if cmd in ("/start", "/hjalp", "/hjälp", "/help"):
        send(chat_id, "Jag är din WizzOS-server 👋\n\n/status – hur servern mår\n/appar – alla appar\n/backup – senaste backupen\n\n"
                      "Allt annat du skriver svarar AI-hjälpen på, t.ex. ”varför är Immich långsam?” eller ”vilken app passar för recept?”. "
                      "Föreslagna åtgärder blir knappar.")
    elif cmd == "/status":
        send(chat_id, _status_text())
    elif cmd == "/appar":
        send(chat_id, _apps_text())
    elif cmd == "/backup":
        send(chat_id, _status_text().split("\n")[-1])
    elif cmd == "/glom":
        _history.pop(chat_id, None)
        send(chat_id, "Jag har glömt vår konversation.")
    else:
        _ask_ai(chat_id, text)


def _loop() -> None:
    global _offset
    while True:
        c = cfg()
        if not c["token"]:
            time.sleep(15)
            continue
        try:
            ups = _call("getUpdates", c["token"], 70, offset=_offset, timeout=50,
                        allowed_updates=["message", "callback_query"])
        except Exception:  # noqa: BLE001
            time.sleep(20)
            continue
        for u in ups:
            _offset = max(_offset, u["update_id"] + 1)
            threading.Thread(target=_handle, args=(u,), daemon=True).start()


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="telegram").start()
