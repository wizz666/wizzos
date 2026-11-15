"""Inloggning via webben (OAuth 2.1) för AI-kopplingen.

Gör att Claude och ChatGPT i mobilen och på webben kan kopplas till WizzOS
utan att du klistrar in någon nyckel: du lägger till serverns adress i
AI-appen, en WizzOS-sida öppnas, du anger ditt lösenord, väljer nivå och
trycker Godkänn. Kopplingen syns sedan bland nycklarna i WizzOS och kan
stängas av där när som helst.

Följer MCP:s auktoriseringsspec: Protected Resource Metadata (RFC 9728),
Authorization Server Metadata (RFC 8414), dynamisk klientregistrering
(RFC 7591), authorization code + PKCE S256, roterande refresh-token.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import secrets
import threading
import time
from urllib.parse import urlencode, urlparse

from . import config

STATE = config.HOME / "oauth.json"
ACCESS_TTL = 3600
CODE_TTL = 300
_lock = threading.Lock()
_codes: dict[str, dict] = {}


def _load() -> dict:
    try:
        return json.loads(STATE.read_text("utf-8"))
    except (OSError, ValueError):
        return {"clients": {}}


def _save(s: dict) -> None:
    import os
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1), "utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(STATE)


def _h(x: str) -> str:
    return hashlib.sha256(x.encode()).hexdigest()


# ---------- metadata ----------

def resource_metadata(base: str) -> dict:
    return {"resource": f"{base}/mcp", "authorization_servers": [base],
            "bearer_methods_supported": ["header"], "resource_name": "WizzOS",
            "scopes_supported": ["wizzos"]}


def server_metadata(base: str) -> dict:
    return {"issuer": base, "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token", "registration_endpoint": f"{base}/oauth/register",
            "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
            "scopes_supported": ["wizzos"], "service_documentation": "https://github.com/wizz666"}


# ---------- klientregistrering ----------

def _ok_redirect(u: str) -> bool:
    p = urlparse(u)
    if p.scheme == "https" and p.hostname:
        return True
    return p.scheme == "http" and p.hostname in ("localhost", "127.0.0.1", "::1")


def register(body: dict) -> dict:
    uris = body.get("redirect_uris") or []
    if not isinstance(uris, list) or not uris or not all(isinstance(u, str) and _ok_redirect(u) for u in uris):
        raise ValueError("invalid_redirect_uri")
    method = body.get("token_endpoint_auth_method") or "none"
    if method not in ("none", "client_secret_post", "client_secret_basic"):
        method = "none"
    cid = "wzc_" + secrets.token_urlsafe(12)
    rec = {"name": str(body.get("client_name") or "AI-app")[:60], "redirect_uris": uris[:5],
           "created": int(time.time()), "method": method}
    out = {"client_id": cid, "client_id_issued_at": rec["created"], "client_name": rec["name"],
           "redirect_uris": rec["redirect_uris"], "grant_types": ["authorization_code", "refresh_token"],
           "response_types": ["code"], "token_endpoint_auth_method": method}
    if method != "none":
        sec = secrets.token_urlsafe(24)
        rec["secret"] = _h(sec)
        out["client_secret"] = sec
        out["client_secret_expires_at"] = 0
    with _lock:
        s = _load()
        # Rensa registreringar som aldrig använts på 30 dagar
        now = time.time()
        s["clients"] = {k: v for k, v in s["clients"].items() if v.get("used") or now - v["created"] < 30 * 86400}
        if len(s["clients"]) > 200:
            raise ValueError("too_many_clients")
        s["clients"][cid] = rec
        _save(s)
    return out


def client(cid: str) -> dict | None:
    return _load()["clients"].get(cid or "")


# ---------- godkännandesidan ----------

def _sig(params: dict) -> str:
    secret = config.load()["secret"].encode()
    msg = json.dumps(params, sort_keys=True).encode()
    return hmac.new(secret, b"oauth:" + msg, hashlib.sha256).hexdigest()


AUTH_KEYS = ("client_id", "redirect_uri", "state", "code_challenge", "code_challenge_method", "scope", "resource")


def check_request(q: dict) -> tuple[dict, dict]:
    """Kontrollera en authorize-förfrågan. ValueError = visa fel (skicka INTE tillbaka till okänd adress)."""
    params = {k: str(q.get(k) or "") for k in AUTH_KEYS}
    c = client(params["client_id"])
    if not c:
        raise ValueError("Okänd AI-app. Lägg till kopplingen på nytt i AI-appen.")
    if params["redirect_uri"] not in c["redirect_uris"]:
        raise ValueError("Adressen AI-appen vill skicka dig tillbaka till stämmer inte.")
    if (q.get("response_type") or "") != "code":
        raise ValueError("AI-appen bad om något WizzOS inte stöder (response_type).")
    if params["code_challenge_method"] != "S256" or len(params["code_challenge"]) < 43:
        raise ValueError("AI-appen saknar säkerhetskontroll (PKCE S256).")
    return params, c


def consent_page(params: dict, c: dict, err: str = "") -> str:
    from .shares import _page
    from . import mcp
    host = urlparse(params["redirect_uri"]).hostname or ""
    hidden = "".join(f"<input type=hidden name='{k}' value='{html.escape(v, quote=True)}'>" for k, v in params.items())
    levels = "".join(
        f"<label class=lv><input type=radio name=level value='{k}' {'checked' if k == 'act' else ''}><span><b>{html.escape(t)}</b>"
        f"<small>{html.escape(d)}</small></span></label>"
        for k, t, d in (("read", "👀 Bara titta", "Status, appar, loggar, backup, säkerhet, kostnad."),
                        ("act", "🛠️ Vanliga saker", "Även installera på prov, starta om, uppdatera säkert, backup, delningslänkar."),
                        ("all", "🔐 Allt – du godkänner", "Även ta bort, återställa, installera permanent – men bara efter ditt ja i mobilen.")))
    extra = """<style>.lv{display:flex;gap:10px;align-items:flex-start;border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin-top:8px;cursor:pointer}
      .lv input{width:auto;margin:4px 0 0;letter-spacing:0}.lv small{display:block;color:var(--muted);font-size:13px}
      select{font:inherit;padding:10px;border-radius:12px;border:1px solid var(--line);background:var(--bg);color:var(--text);width:100%;margin-top:6px}
      input[type=password]{letter-spacing:1px;font-size:16px;text-align:left}.who{font-size:13px;color:var(--muted);word-break:break-all}
      .row2{display:flex;gap:10px;margin-top:16px}.ghost{background:#3a425e!important}</style>"""
    return _page("Koppla AI till WizzOS", extra + f"""<div class=card>
      <h1>🤖 {html.escape(c['name'])} vill koppla till WizzOS</h1>
      <p class=who>Efter godkännandet skickas du tillbaka till <b>{html.escape(host)}</b>.</p>
      <p class=m>{html.escape(mcp.LEVELS.get('all', ''))} kräver alltid ditt ja i mobilen för det som är svårt att ångra. Lösenord och backupnycklar lämnas aldrig ut.</p>
      <form method=post action='/oauth/authorize'>{hidden}<input type=hidden name=sig value='{_sig(params)}'>
        <label><b>Vad får den göra?</b></label>{levels}
        <label style='display:block;margin-top:14px'><b>Hur länge?</b></label>
        <select name=days><option value=30>30 dagar</option><option value=90 selected>90 dagar</option><option value=365>1 år</option><option value=0>Tills jag stänger av den</option></select>
        <label style='display:block;margin-top:14px'><b>Ditt WizzOS-lösenord</b></label>
        <input type=password name=password autocomplete=current-password required autofocus>
        {f"<p class=err>{html.escape(err)}</p>" if err else ""}
        <div class=row2><button class=btn type=submit name=go value=yes>✅ Godkänn</button>
        <button class='btn ghost' type=submit name=go value=no formnovalidate>Avbryt</button></div>
      </form></div>""")


def error_page(msg: str) -> str:
    from .shares import _page
    return _page("Kunde inte koppla", f"<div class=card><h1>Kunde inte koppla</h1><p class=m>{html.escape(msg)}</p></div>")


def _redirect(uri: str, **q) -> str:
    sep = "&" if "?" in uri else "?"
    return uri + sep + urlencode({k: v for k, v in q.items() if v})


def decide(form: dict, ip: str) -> tuple[str, str]:
    """→ ("redirect", url) eller ("page", html)."""
    from . import auth
    params = {k: str(form.get(k) or "") for k in AUTH_KEYS}
    if not hmac.compare_digest(_sig(params), str(form.get("sig") or "")):
        return "page", error_page("Formuläret har ändrats. Börja om från AI-appen.")
    c = client(params["client_id"])
    if not c or params["redirect_uri"] not in c["redirect_uris"]:
        return "page", error_page("Okänd AI-app.")
    if form.get("go") != "yes":
        return "redirect", _redirect(params["redirect_uri"], error="access_denied", state=params["state"])
    if auth.too_many_failures(ip):
        return "page", consent_page(params, c, "För många försök. Vänta fem minuter.")
    if not auth.check_password(str(form.get("password") or "")):
        auth.note_failure(ip)
        return "page", consent_page(params, c, "Fel lösenord.")
    level = form.get("level") if form.get("level") in ("read", "act", "all") else "read"
    days = int(form.get("days") or 90) if str(form.get("days") or "90") in ("0", "30", "90", "365") else 90
    code = secrets.token_urlsafe(32)
    with _lock:
        now = time.time()
        for k in [k for k, v in _codes.items() if v["exp"] < now]:
            _codes.pop(k, None)
        _codes[code] = {**params, "level": level, "days": days, "name": c["name"], "exp": now + CODE_TTL}
    return "redirect", _redirect(params["redirect_uri"], code=code, state=params["state"])


# ---------- token ----------

class OAuthError(Exception):
    def __init__(self, error: str, desc: str = "", status: int = 400):
        super().__init__(desc or error)
        self.error, self.desc, self.status = error, desc, status


def _client_auth(form: dict, basic: tuple[str, str] | None) -> dict:
    cid = form.get("client_id") or (basic[0] if basic else "")
    c = client(cid)
    if not c:
        raise OAuthError("invalid_client", "Okänd klient.", 401)
    if c.get("secret"):
        sec = form.get("client_secret") or (basic[1] if basic else "")
        if not sec or not hmac.compare_digest(_h(sec), c["secret"]):
            raise OAuthError("invalid_client", "Fel klienthemlighet.", 401)
    return {"id": cid, **c}


def token(form: dict, basic: tuple[str, str] | None) -> dict:
    from . import mcp
    gt = form.get("grant_type")
    c = _client_auth(form, basic)
    if gt == "authorization_code":
        with _lock:
            rec = _codes.pop(form.get("code") or "", None)
        if not rec or rec["exp"] < time.time() or rec["client_id"] != c["id"]:
            raise OAuthError("invalid_grant", "Koden är ogiltig eller för gammal.")
        if form.get("redirect_uri") and form["redirect_uri"] != rec["redirect_uri"]:
            raise OAuthError("invalid_grant", "redirect_uri stämmer inte.")
        ver = form.get("code_verifier") or ""
        chal = base64.urlsafe_b64encode(hashlib.sha256(ver.encode()).digest()).rstrip(b"=").decode()
        if not ver or not hmac.compare_digest(chal, rec["code_challenge"]):
            raise OAuthError("invalid_grant", "PKCE-kontrollen misslyckades.")
        access, refresh, _ = mcp.create_oauth_grant(f"{rec['name']} (via webben)", rec["level"], rec["days"], c["id"])
        with _lock:
            s = _load()
            if c["id"] in s["clients"]:
                s["clients"][c["id"]]["used"] = int(time.time())
                _save(s)
    elif gt == "refresh_token":
        access, refresh = mcp.refresh_oauth_grant(form.get("refresh_token") or "", c["id"])
        if not access:
            raise OAuthError("invalid_grant", "Kopplingen är avstängd eller har gått ut. Koppla på nytt.")
    else:
        raise OAuthError("unsupported_grant_type")
    return {"access_token": access, "token_type": "Bearer", "expires_in": ACCESS_TTL,
            "refresh_token": refresh, "scope": "wizzos"}
