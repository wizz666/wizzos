"""Dela med utgångsdatum.

En länk till en fil eller mapp som andra kan öppna utan konto. Den slutar
fungera av sig själv (1 dag – 90 dagar), kan skyddas med en kod och kan
stoppas när som helst. Mottagaren ser bara det som delats – aldrig något
utanför mappen och aldrig WizzOS själv.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import secrets
import threading
import time
from pathlib import Path

from . import config, files

STATE = config.HOME / "shares.json"
CHOICES = {1: "1 dag", 7: "1 vecka", 30: "30 dagar", 90: "90 dagar"}
_lock = threading.Lock()
_fails: dict[str, list[float]] = {}


def _load() -> dict:
    try:
        return json.loads(STATE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _save(s: dict) -> None:
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(STATE)


def _pin_hash(pin: str, salt: str) -> str:
    return hashlib.scrypt(pin.encode(), salt=salt.encode(), n=2**14, r=8, p=1, dklen=24).hex()


def create(path: str, days: int, pin: str = "", note: str = "", by: str = "") -> dict:
    p = files.resolve(path)
    if not p.exists():
        raise ValueError("Det finns inget att dela där.")
    days = int(days)
    if days not in CHOICES:
        raise ValueError("Välj hur länge länken ska fungera.")
    pin = (pin or "").strip()
    if pin and (not pin.isdigit() or not 4 <= len(pin) <= 8):
        raise ValueError("Koden ska vara 4–8 siffror.")
    token = secrets.token_urlsafe(18)
    salt = secrets.token_hex(8)
    rec = {"path": files.to_rel(p), "name": p.name, "dir": p.is_dir(), "created": int(time.time()),
           "expires": int(time.time()) + days * 86400, "days": days, "downloads": 0,
           "pin": _pin_hash(pin, salt) if pin else "", "salt": salt, "note": note[:300], "by": by}
    with _lock:
        s = _load()
        s[token] = rec
        _save(s)
    return {"token": token, **public(token, rec)}


def public(token: str, r: dict) -> dict:
    return {"token": token, "path": r["path"], "name": r["name"], "dir": r["dir"], "created": r["created"],
            "expires": r["expires"], "downloads": r["downloads"], "has_pin": bool(r["pin"]),
            "note": r.get("note", ""), "by": r.get("by", ""), "url_path": f"/s/{token}"}


def active() -> list[dict]:
    now = time.time()
    return sorted([public(t, r) for t, r in _load().items() if not r["expires"] or r["expires"] > now],
                  key=lambda x: x["created"], reverse=True)


def stop(token: str) -> None:
    with _lock:
        s = _load()
        if s.pop(token, None) is None:
            raise ValueError("Delningen finns inte.")
        _save(s)


def prune() -> None:
    now = time.time()
    with _lock:
        s = _load()
        keep = {t: r for t, r in s.items() if not r["expires"] or r["expires"] > now - 7 * 86400}
        if len(keep) != len(s):
            _save(keep)


def get(token: str) -> dict | None:
    r = _load().get(token)
    if not r or (r["expires"] and r["expires"] < time.time()):
        return None
    return r


def unlock_cookie(token: str) -> str:
    secret = config.load()["secret"].encode()
    return hmac.new(secret, b"share:" + token.encode(), hashlib.sha256).hexdigest()[:32]


def check_pin(token: str, pin: str, ip: str) -> bool:
    now = time.time()
    key = f"{ip}:{token}"
    recent = [t for t in _fails.get(key, []) if now - t < 900]
    if len(recent) >= 6:
        raise ValueError("För många försök. Vänta en kvart.")
    r = get(token)
    if r and r["pin"] and hmac.compare_digest(_pin_hash(pin.strip(), r["salt"]), r["pin"]):
        return True
    _fails[key] = recent + [now]
    return False


def target(token: str, rel: str = "") -> Path:
    """Sökväg inom delningen. Allt utanför nekas."""
    r = get(token)
    if not r:
        raise FileNotFoundError
    base = files.resolve(r["path"])
    if not r["dir"]:
        return base
    p = (base / (rel or "").lstrip("/")).resolve()
    if p != base and base not in p.parents:
        raise FileNotFoundError
    files.resolve(files.to_rel(p))  # samma skydd som filhanteraren (/DATA, inte WizzOS)
    return p


def count_download(token: str) -> None:
    with _lock:
        s = _load()
        if token in s:
            s[token]["downloads"] += 1
            _save(s)


# ---------- sidan mottagaren ser ----------

CSS = """
:root{--bg:#0b1020;--card:#141a2e;--line:#232c47;--text:#e9edf7;--muted:#9aa5c3;--a:#6d8cff;--b:#b56dff}
@media (prefers-color-scheme: light){:root{--bg:#f4f6fb;--card:#fff;--line:#e3e7f1;--text:#141a2e;--muted:#5d6785}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.w{max-width:720px;margin:0 auto;padding:32px 16px}
.brand{display:flex;align-items:center;gap:10px;color:var(--muted);font-size:14px;margin-bottom:22px}.brand img{width:28px;height:28px;border-radius:8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:22px}
h1{font-size:24px;margin:0 0 4px;word-break:break-word}.m{color:var(--muted);font-size:14px}
.note{margin:14px 0 0;padding:12px 14px;border-left:3px solid var(--a);background:rgba(109,140,255,.08);border-radius:8px;white-space:pre-wrap}
.btn{display:inline-flex;align-items:center;gap:8px;padding:11px 18px;border-radius:12px;border:0;font:inherit;font-weight:600;
 background:linear-gradient(135deg,var(--a),var(--b));color:#fff;text-decoration:none;cursor:pointer;margin-top:16px}
ul{list-style:none;padding:0;margin:16px 0 0}li{display:flex;justify-content:space-between;gap:12px;padding:10px 4px;border-top:1px solid var(--line)}
li a{color:var(--text);text-decoration:none;word-break:break-all}li a:hover{color:var(--a)}li span{color:var(--muted);font-size:13px;white-space:nowrap}
input{font:inherit;padding:11px 14px;border-radius:12px;border:1px solid var(--line);background:var(--bg);color:var(--text);width:100%;margin-top:12px;letter-spacing:4px;text-align:center;font-size:22px}
.err{color:#ff6d8a;margin-top:10px}.crumb{font-size:14px;margin-top:6px}.crumb a{color:var(--a)}
"""


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html lang=sv><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<meta name=robots content='noindex,nofollow'><title>{html.escape(title)}</title><style>{CSS}</style>"
            f"<div class=w><div class=brand><img src='/static/logo-192.png' alt=''>Delat från en WizzOS-server</div>{body}</div></html>")


def _left(exp: int) -> str:
    s = exp - time.time()
    d = int(s // 86400)
    return f"{d} dagar" if d >= 2 else f"{int(s // 3600)} timmar" if s >= 3600 else f"{max(1, int(s // 60))} minuter"


def gone_page() -> str:
    return _page("Länken har gått ut", "<div class=card><h1>Länken fungerar inte längre</h1>"
                 "<p class=m>Den har gått ut eller stoppats av den som delade. Be om en ny länk.</p></div>")


def pin_page(token: str, err: str = "") -> str:
    return _page("Ange kod", f"""<div class=card><h1>🔒 Ange koden</h1><p class=m>Den som delade har skyddat det här med en kod.</p>
      <form method=post action='/s/{html.escape(token)}/unlock'><input name=pin inputmode=numeric autocomplete=one-time-code
      maxlength=8 autofocus aria-label=Kod>{f"<p class=err>{html.escape(err)}</p>" if err else ""}
      <button class=btn type=submit>Öppna</button></form></div>""")


def view_page(token: str, rel: str = "") -> str:
    r = get(token)
    p = target(token, rel)
    t = html.escape(token)
    note = f"<div class=note>{html.escape(r['note'])}</div>" if r.get("note") else ""
    meta = f"<p class=m>Länken fungerar i {_left(r['expires'])} till.</p>"
    if not r["dir"]:
        size = files.fmt_size(p.stat().st_size) if hasattr(files, "fmt_size") else f"{p.stat().st_size // 1024} KB"
        return _page(r["name"], f"""<div class=card><h1>📄 {html.escape(r['name'])}</h1><p class=m>{size}</p>{meta}{note}
          <a class=btn href='/s/{t}/dl'>⬇ Ladda ner</a></div>""")
    from urllib.parse import quote
    entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    rows = []
    for e in entries:
        if e.is_symlink() or e.name.startswith("."):
            continue
        sub = (rel.rstrip("/") + "/" + e.name).lstrip("/")
        if e.is_dir():
            rows.append(f"<li><a href='/s/{t}?p={quote(sub)}'>📁 {html.escape(e.name)}</a><span>mapp</span></li>")
        elif e.is_file():
            kb = e.stat().st_size
            sz = f"{kb / 1e9:.1f} GB" if kb > 1e9 else f"{kb / 1e6:.1f} MB" if kb > 1e6 else f"{max(1, kb // 1000)} kB"
            rows.append(f"<li><a href='/s/{t}/dl?p={quote(sub)}'>📄 {html.escape(e.name)}</a><span>{sz}</span></li>")
    crumb = ""
    if rel.strip("/"):
        parts = rel.strip("/").split("/")
        up = "/".join(parts[:-1])
        crumb = f"<p class=crumb><a href='/s/{t}{('?p=' + quote(up)) if up else ''}'>← Tillbaka</a> · {html.escape(rel.strip('/'))}</p>"
    zip_href = f"/s/{t}/dl?p={quote(rel.strip('/'))}&zip=1" if rel.strip("/") else f"/s/{t}/dl?zip=1"
    return _page(r["name"], f"""<div class=card><h1>📁 {html.escape(r['name'])}</h1>{crumb}{meta}{note}
      <a class=btn href='{zip_href}'>⬇ Ladda ner allt (zip)</a>
      <ul>{''.join(rows) or '<li><span>Mappen är tom.</span></li>'}</ul></div>""")


_started = False


def start_cleaner() -> None:
    global _started
    if _started:
        return
    _started = True

    def loop():
        while True:
            try:
                prune()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(3600)

    threading.Thread(target=loop, daemon=True, name="share-prune").start()
