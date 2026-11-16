"""Sök i hela servern: appar, filer, WizzOS-inställningar, appbutiken – och webben.

Filnamnen hålls i ett index i minnet som byggs om i bakgrunden (var 20:e minut
och efter start), så sökningen svarar direkt även med hundratusentals filer.
Webbsökningen går via din egen SearXNG (WizzOS-paketet) – ingen spårning.
"""
from __future__ import annotations

import os
import threading
import time
from urllib.parse import quote_plus

import httpx

from . import apps, config

MAX_FILES = 400_000
SKIP_TOP = {"WizzOS", "AppData", "lost+found"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".cache", ".Trash-1000", "@eaDir", ".thumbnails"}
_index: list[tuple[str, str, int, int, bool]] = []  # (rel, namn_lower, storlek, mtime, mapp)
_meta = {"built": 0, "count": 0, "secs": 0.0, "truncated": False, "building": False}
_lock = threading.Lock()

# Platser i WizzOS man kan söka fram (titel, länk, sökord)
PLACES = [
    ("Hem", "#/", "start översikt hem home start overview"),
    ("Appbutik", "#/store", "butik installera ny app appar store app store install new apps"),
    ("Filer", "#/files", "filhanterare filer mappar ladda upp files file manager folders upload"),
    ("Papperskorgen", "#/files", "papperskorg raderade återställ fil trash deleted restore file recycle bin"),
    ("Backup", "#/backup", "backup säkerhetskopia återställ restic r2 backup restore"),
    ("Trygghet", "#/trygghet", "trygghet säkerhet safety security"),
    ("Säkerhetskoll", "#/trygghet", "säkerhet säkerhetskoll betyg ssh lösenord öppen security check grade ssh password open ports"),
    ("Delningar", "#/trygghet", "dela delning länk utgångsdatum share sharing link expiry"),
    ("Om jag försvinner", "#/trygghet", "försvinner arv brev betrodd död testamente disappear legacy letter trusted death will dead man switch"),
    ("Larm till mobilen", "#/settings", "larm notis ntfy mobil varning alerts notification phone warning"),
    ("Nattliga uppdateringar", "#/settings", "uppdatering uppdateringar natt automatisk version updates nightly automatic version"),
    ("Veckobrev", "#/settings", "veckobrev brev vecka sammanfattning weekly letter summary"),
    ("E-post", "#/settings", "epost e-post mejl mail smtp gmail email"),
    ("Home Assistant", "#/settings", "home assistant ha sensorer sensors"),
    ("Nestward", "#/settings", "nestward hemmet hälsa maskiner home health machines"),
    ("AI-koppling", "#/settings", "ai mcp claude chatgpt nyckel koppling godkänn key link approve connector"),
    ("AI-hjälp", "#/settings", "ai hjälp assistent fråga help assistant ask"),
    ("Egen adress (https)", "#/settings", "adress domän https cloudflare certifikat proxy address domain certificate"),
    ("Vem når apparna?", "#/settings", "nätverk tailscale bind ip nå appar network reach apps"),
    ("Appbutiker", "#/settings", "butiker appbutiker lägg till butik stores app stores add store"),
    ("Lösenord", "#/settings", "lösenord byt lösenord inloggning password change password login"),
    ("Flytta servern", "#/settings", "flytta server ny dator flyttkod move server new computer migrate move code"),
    ("WizzOS i mobilen", "#/settings", "mobil qr hemskärm telefon mobile phone home screen"),
    ("Elkostnad", "#/", "el kostnad kronor ström watt elpris electricity cost power price energy"),
    ("Dokument och Paperless", "#/settings", "dokument sök innehåll pdf paperless ocr index documents search content"),
]


def _norm(s: str) -> str:
    return s.lower()


def build_index() -> None:
    if _meta["building"]:
        return
    _meta["building"] = True
    t0 = time.time()
    out: list[tuple[str, str, int, int, bool]] = []
    root = str(config.DATA_ROOT)
    truncated = False
    try:
        stack = [root]
        while stack and not truncated:
            d = stack.pop()
            try:
                with os.scandir(d) as it:
                    for e in it:
                        if d == root and e.name in SKIP_TOP:
                            continue
                        if e.name in SKIP_DIRS:
                            continue
                        try:
                            is_dir = e.is_dir(follow_symlinks=False)
                            st = e.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        rel = e.path[len(root):] or "/"
                        out.append((rel, _norm(e.name), 0 if is_dir else st.st_size, int(st.st_mtime), is_dir))
                        if is_dir:
                            stack.append(e.path)
                        if len(out) >= MAX_FILES:
                            truncated = True
                            break
            except OSError:
                continue
    finally:
        with _lock:
            _index[:] = out
            _meta.update(built=int(time.time()), count=len(out), secs=round(time.time() - t0, 2),
                         truncated=truncated, building=False)


def _files(q: str, limit: int) -> list[dict]:
    words = [w for w in _norm(q).split() if w]
    if not words:
        return []
    with _lock:
        idx = list(_index)
    hits = []
    for rel, name, size, mtime, is_dir in idx:
        hay = name if len(words) == 1 else rel.lower()
        if all(w in hay for w in words):
            exact = name == words[0] or name.rsplit(".", 1)[0] == words[0]
            hits.append((0 if exact else (1 if name.startswith(words[0]) else 2), -mtime, rel, name, size, mtime, is_dir))
            if len(hits) > 2000:
                break
    hits.sort()
    return [{"path": h[2], "name": os.path.basename(h[2]), "size": h[4], "mtime": h[5], "dir": h[6]} for h in hits[:limit]]


def _apps(q: str, inst: list[dict]) -> list[dict]:
    ql = _norm(q)
    out = []
    for a in inst:
        hay = " ".join(str(a.get(k) or "") for k in ("title", "name", "tagline", "category")).lower()
        if all(w in hay for w in ql.split()):
            out.append({"name": a["name"], "title": a["title"], "icon": a.get("icon", ""), "url": a.get("url", ""),
                        "status": a.get("status"), "trial": bool(a.get("trial"))})
    return out[:8]


def _places(q: str) -> list[dict]:
    ql = _norm(q)
    out = []
    for title, link, kw in PLACES:
        hay = (title + " " + kw).lower()
        if all(w in hay for w in ql.split()):
            out.append({"title": title, "link": link})
    return out[:6]


def _store(q: str, inst: list[dict]) -> list[dict]:
    from . import store
    ql = _norm(q)
    installed = {a.get("store_key") for a in inst}
    out = []
    for a in store.all_apps(config.load()["stores"]):
        hay = " ".join(str(a.get(k) or "") for k in ("title", "name", "tagline", "category")).lower()
        if all(w in hay for w in ql.split()) and a["key"] not in installed:
            out.append({"key": a["key"], "title": a["title"], "icon": a.get("icon", ""), "tagline": (a.get("tagline") or "")[:120]})
    out.sort(key=lambda x: (ql not in x["title"].lower(), x["title"]))
    return out[:5]


def _searx(inst: list[dict] | None = None) -> dict | None:
    hits = [a for a in (inst if inst is not None else apps.installed()["apps"]) if a.get("store_key") == "wizzos/searxng"]
    hits.sort(key=lambda a: (bool(a.get("trial")), a.get("status") != "running"))
    return hits[0] if hits else None


def searx_url(inst: list[dict] | None = None) -> str:
    a = _searx(inst)
    if not a or a.get("status") != "running" or not a.get("port"):
        return ""
    host = config.load().get("bind_ip") or "172.17.0.1"
    return f"http://{host}:{a['port']}"


def public_searx_url(inst: list[dict] | None = None) -> str:
    a = _searx(inst)
    return (a.get("url") or "").rstrip("/") if a else ""


def web(q: str, limit: int = 6, inst: list[dict] | None = None) -> dict:
    u = searx_url(inst)
    if not u:
        return {"available": False, "results": []}
    try:
        r = httpx.get(f"{u}/search", params={"q": q, "format": "json", "language": "sv-SE"}, timeout=8)
        r.raise_for_status()
        d = r.json()
        res = [{"title": x.get("title", ""), "url": x.get("url", ""), "content": (x.get("content") or "")[:220],
                "engine": ", ".join(x.get("engines", [])[:2])} for x in d.get("results", [])[:limit]]
        ans = (d.get("answers") or [None])[0]
        return {"available": True, "results": res, "answer": ans if isinstance(ans, str) else (ans or {}).get("answer", ""),
                "more": f"{public_searx_url(inst)}/search?q={quote_plus(q)}" if public_searx_url(inst) else ""}
    except Exception as e:  # noqa: BLE001
        return {"available": True, "results": [], "error": f"Sökmotorn svarade inte ({type(e).__name__})."}


def search(q: str, include_web: bool = True) -> dict:
    q = (q or "").strip()[:200]
    inst = apps.installed()["apps"]
    if len(q) < 2:
        return {"q": q, "apps": [], "places": [], "files": [], "store": [], "web": {"available": bool(searx_url(inst)), "results": []}}
    if not _meta["built"] and not _meta["building"]:
        threading.Thread(target=build_index, daemon=True).start()
    from . import docindex, paperless
    out = {"q": q, "apps": _apps(q, inst), "places": _places(q), "files": _files(q, 12), "store": _store(q, inst),
           "docs": docindex.search(q, 8), "index": {k: v for k, v in _meta.items()},
           "paperless_on": paperless.public_cfg()["configured"]}
    # Det som går över nätet (webben, Paperless) kommer i andra anropet så att resten svarar direkt.
    out["web"] = web(q, inst=inst) if include_web else {"available": bool(searx_url(inst)), "results": []}
    out["paperless"] = paperless.search(q) if include_web else []
    return out


_started = False


def start_indexer() -> None:
    global _started
    if _started:
        return
    _started = True

    def loop():
        time.sleep(15)
        while True:
            try:
                build_index()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(20 * 60)

    threading.Thread(target=loop, daemon=True, name="finder-index").start()
