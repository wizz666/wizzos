"""Paperless-ngx: sök i dina inskannade papper (med OCR) direkt från WizzOS.

Valfritt. Ange adressen till Paperless och en API-token (Paperless → din
profil → "API-token"). Token sparas bara på servern.
"""
from __future__ import annotations

import time

import httpx

from . import config

DEFAULTS = {"url": "", "token": ""}
_status = {"ok": None, "error": "", "checked": 0}


def cfg() -> dict:
    return {**DEFAULTS, **(config.load().get("paperless") or {})}


def public_cfg() -> dict:
    c = cfg()
    return {"url": c["url"], "has_token": bool(c["token"]), "configured": bool(c["url"] and c["token"]), **_status}


def save(url: str, token: str | None) -> dict:
    url = (url or "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        raise ValueError("Adressen ska börja med http:// eller https://")
    c = cfg()
    c["url"] = url
    if token and token.strip():
        c["token"] = token.strip()
    if not url:
        c["token"] = ""
    config.update(paperless=c)
    return public_cfg()


def _get(path: str, params: dict | None = None, timeout: float = 6) -> dict:
    c = cfg()
    r = httpx.get(f"{c['url']}{path}", params=params, timeout=timeout, follow_redirects=True,
                  headers={"Authorization": f"Token {c['token']}", "Accept": "application/json; version=5"})
    if r.status_code in (401, 403):
        raise ValueError("Paperless godkände inte token.")
    r.raise_for_status()
    return r.json()


def test() -> dict:
    c = cfg()
    if not (c["url"] and c["token"]):
        raise ValueError("Fyll i adress och token först.")
    try:
        d = _get("/api/documents/", {"page_size": 1})
    except ValueError:
        raise
    except Exception as e:  # noqa: BLE001
        _status.update(ok=False, error=f"Paperless svarar inte ({type(e).__name__}).", checked=int(time.time()))
        raise ValueError(_status["error"])
    _status.update(ok=True, error="", checked=int(time.time()))
    return {"ok": True, "documents": d.get("count", 0)}


def search(q: str, limit: int = 5) -> list[dict]:
    c = cfg()
    if not (c["url"] and c["token"]) or len(q.strip()) < 2:
        return []
    try:
        d = _get("/api/documents/", {"query": q, "page_size": limit,
                                     "fields": "id,title,created,correspondent,__search_hit__"})
    except Exception as e:  # noqa: BLE001
        _status.update(ok=False, error=str(e)[:120], checked=int(time.time()))
        return []
    out = []
    for x in d.get("results", [])[:limit]:
        hit = x.get("__search_hit__") or {}
        hl = (hit.get("highlights") or "").replace('<span class="match">', "\x02").replace("</span>", "\x03")
        import re
        hl = re.sub(r"<[^>]+>", "", hl)
        out.append({"id": x["id"], "title": x.get("title", ""), "created": (x.get("created") or "")[:10],
                    "snippet": hl[:300], "url": f"{c['url']}/documents/{x['id']}/details"})
    return out
