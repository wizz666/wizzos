"""Godkännandekö: AI:n frågar, du bestämmer – i mobilen eller i WizzOS.

När en AI med nivån "Allt" vill göra något större (ta bort en app, återställa,
installera permanent …) skapas en fråga. Den skickas som notis till mobilen
med knapparna Godkänn / Neka och syns också överst på startsidan. Inget händer
förrän du svarat. Svarar du inte inom 15 minuter förfaller frågan.

Varje fråga har en egen engångsnyckel i länken – den fungerar bara för just den
frågan och bara en gång.
"""
from __future__ import annotations

import hmac
import json
import secrets
import threading
import time

from . import config

STATE = config.HOME / "approvals.json"
TTL = 15 * 60
_lock = threading.Lock()
EXECUTORS: dict = {}  # verktygsnamn → fn(args) -> dict (fylls i av mcp.py)


def _load() -> dict:
    try:
        return json.loads(STATE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _save(s: dict) -> None:
    # Behåll de senaste 100
    if len(s) > 100:
        for k in sorted(s, key=lambda k: s[k]["created"])[:-100]:
            s.pop(k)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1, default=str), "utf-8")
    import os
    os.chmod(tmp, 0o600)
    tmp.replace(STATE)


def _expire(s: dict) -> bool:
    changed = False
    now = time.time()
    for r in s.values():
        if r["status"] == "pending" and r["expires"] < now:
            r["status"] = "expired"
            changed = True
    return changed


def base_url() -> str:
    from . import proxy
    s = config.load()
    # Publik AI-adress (tunnel) först – då fungerar knapparna även utan Tailscale.
    return (s.get("ai_public_url") or proxy.wizz_url() or s.get("phone_base") or "").rstrip("/")


def public(r: dict) -> dict:
    return {k: v for k, v in r.items() if k != "token"}


def create(who: str, tool: str, args: dict, title: str, detail: str) -> dict:
    aid = secrets.token_hex(4)
    token = secrets.token_urlsafe(24)
    r = {"id": aid, "token": token, "who": who, "tool": tool, "args": args, "title": title, "detail": detail,
         "created": int(time.time()), "expires": int(time.time()) + TTL, "status": "pending",
         "decided_via": "", "decided": 0, "result": None, "error": ""}
    with _lock:
        s = _load()
        _expire(s)
        s[aid] = r
        _save(s)
    _push(r)
    return public(r)


def _push(r: dict) -> None:
    from . import alerts
    c = alerts.cfg()
    if not (c.get("ntfy_url") and c.get("ntfy_topic")):
        return
    base = base_url()
    actions = []
    if base:
        u = f"{base}/approve/{r['token']}"
        actions = [{"action": "http", "label": "✅ Godkänn", "url": u + "/yes", "method": "POST", "clear": True},
                   {"action": "http", "label": "⛔ Neka", "url": u + "/no", "method": "POST", "clear": True},
                   {"action": "view", "label": "Visa", "url": u}]
    try:
        alerts.send(f"{r['who']} vill: {r['title']}",
                    f"{r['detail']}\n\nSvara inom 15 minuter – annars händer ingenting.", 4, "robot",
                    actions=actions, click=f"{base}/approve/{r['token']}" if base else "")
    except Exception:  # noqa: BLE001
        pass


def pending() -> list[dict]:
    with _lock:
        s = _load()
        if _expire(s):
            _save(s)
    return [public(r) for r in sorted(s.values(), key=lambda r: r["created"]) if r["status"] == "pending"]


def recent(n: int = 20) -> list[dict]:
    s = _load()
    _expire(s)
    return [public(r) for r in sorted(s.values(), key=lambda r: -r["created"])[:n]]


def get(aid: str) -> dict | None:
    s = _load()
    _expire(s)
    r = s.get(aid)
    return public(r) if r else None


def by_token(token: str) -> dict | None:
    s = _load()
    _expire(s)
    for r in s.values():
        if hmac.compare_digest(r["token"], token):
            return r
    return None


def decide(aid: str, yes: bool, via: str) -> dict:
    """Godkänn eller neka. Vid ja körs åtgärden direkt här på servern."""
    with _lock:
        s = _load()
        _expire(s)
        r = s.get(aid)
        if not r:
            raise ValueError("Frågan finns inte.")
        if r["status"] != "pending":
            raise ValueError({"approved": "Redan godkänd.", "done": "Redan godkänd och utförd.",
                              "denied": "Redan nekad.", "expired": "Frågan har gått ut (15 minuter).",
                              "failed": "Redan godkänd, men det misslyckades."}.get(r["status"], "Redan besvarad."))
        r["status"] = "approved" if yes else "denied"
        r["decided_via"] = via
        r["decided"] = int(time.time())
        _save(s)
    from . import mcp
    mcp.diary(r["who"], r["tool"], r["args"], yes, ("Godkänt" if yes else "Nekat") + f" ({via}): {r['title']}")
    if yes:
        # Utför i bakgrunden – mobilens knapp ska få svar direkt.
        threading.Thread(target=_execute, args=(aid, r), daemon=True, name=f"approve-{aid}").start()
    return get(aid)


def _execute(aid: str, r: dict) -> None:
    from . import alerts, mcp
    fn = EXECUTORS.get(r["tool"])
    try:
        res = fn(r["args"]) if fn else {"fel": "okänt verktyg"}
        if isinstance(res, dict) and res.get("state") == "misslyckades":
            raise RuntimeError(res.get("error") or "Jobbet misslyckades.")
        status, err = "done", ""
    except Exception as e:  # noqa: BLE001
        res, status, err = None, "failed", str(e)
    with _lock:
        s = _load()
        s[aid].update(status=status, result=res, error=err)
        _save(s)
    mcp.diary(r["who"], r["tool"], r["args"], status == "done",
              (f"Utfört: {r['title']}" if status == "done" else f"Misslyckades: {err}"))
    if status == "failed":
        try:
            alerts.notify("Det gick inte", f"{r['title']}: {err}")
        except Exception:  # noqa: BLE001
            pass


def wait(aid: str, seconds: int) -> dict:
    end = time.time() + seconds
    while time.time() < end:
        r = get(aid)
        if not r or r["status"] not in ("pending", "approved"):
            return r
        time.sleep(1.5)
    return get(aid)


# ---------- sidan i mobilen ----------

def page(token: str, msg: str = "") -> str:
    from html import escape
    from .shares import _page
    r = by_token(token)
    if not r:
        return _page("Frågan finns inte", "<div class=card><h1>Frågan finns inte</h1><p class=m>Länken är fel eller för gammal.</p></div>")
    st = {"pending": "", "approved": "✅ Godkänd – utförs …", "done": "✅ Godkänd och utförd.", "denied": "⛔ Nekad. Ingenting gjordes.",
          "expired": "⌛ Frågan gick ut efter 15 minuter. Ingenting gjordes.", "failed": f"⚠️ Godkänd, men det misslyckades: {r['error']}"}[r["status"]]
    left = max(0, int((r["expires"] - time.time()) // 60))
    body = (f"<div class=card><p class=m>🤖 {escape(r['who'])} vill</p><h1>{escape(r['title'])}</h1>"
            f"<p style='white-space:pre-wrap'>{escape(r['detail'])}</p>")
    if msg:
        body += f"<p class=err>{escape(msg)}</p>"
    if r["status"] == "pending":
        body += (f"<p class=m>Gäller {left} minuter till.</p>"
                 f"<form method=post action='/approve/{escape(token)}/yes' style='display:inline'><button class=btn type=submit>✅ Godkänn</button></form> "
                 f"<form method=post action='/approve/{escape(token)}/no' style='display:inline'><button class=btn type=submit "
                 f"style='background:#3a425e'>⛔ Neka</button></form>")
    else:
        body += f"<p><b>{escape(st)}</b></p>"
    return _page("Godkänn?", body + "</div>")
