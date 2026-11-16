"""Hämtar och läser CasaOS-kompatibla appbutiker.

En butik är ett GitHub-repo (eller en zip) med Apps/<Namn>/docker-compose.yml
där toppnivån har ett `x-casaos`-block med titel, ikon, port osv.
"""
from __future__ import annotations

import asyncio
import json
import re
import tempfile
import time
import zipfile

import httpx
import yaml

from . import config

GH_ZIP = re.compile(
    r"https?://github\.com/([^/]+)/([^/]+)/archive/(?:refs/heads/)?([^/]+?)\.zip$")
GH_REPO = re.compile(r"https?://github\.com/([^/]+)/([^/#?]+?)(?:\.git)?/?(?:tree/([^/]+))?$")
UA = {"User-Agent": "WizzOS-appstore"}

_cache: dict[str, dict] = {}
EN = ("en_US", "en_GB", "en_us", "en", "sv_SE")


def localized(app: dict) -> dict:
    """Byt till engelska texter om den som frågar använder engelska."""
    from . import i18n
    if i18n.current() == "en" and app.get("en"):
        return {**app, **{k: v for k, v in app["en"].items() if v}}
    return app


def pick(value, prefer=("sv_SE", "en_US", "en_GB", "en_us", "en")) -> str:
    """Välj svensk text om den finns, annars engelsk, annars första."""
    if isinstance(value, dict):
        for k in prefer:
            if value.get(k):
                return str(value[k]).strip()
        for v in value.values():
            if v:
                return str(v).strip()
        return ""
    return str(value or "").strip()


def store_file(store_id: str):
    return config.STORES_DIR / f"{store_id}.json"


def parse_compose(text: str, store_id: str, folder: str) -> dict | None:
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("services"), dict):
        return None
    x = doc.get("x-casaos") or {}
    services = doc["services"]
    main = x.get("main") or next(iter(services))
    name = str(doc.get("name") or folder).lower()
    name = re.sub(r"[^a-z0-9_-]", "-", name)
    shots = x.get("screenshot_link") or []
    if isinstance(shots, str):
        shots = [shots]
    archs = [str(a) for a in (x.get("architectures") or [])]
    return {
        "key": f"{store_id}/{folder}",
        "store": store_id,
        "name": name,
        "title": pick(x.get("title")) or folder,
        "tagline": pick(x.get("tagline")),
        "description": pick(x.get("description")),
        "tips": pick((x.get("tips") or {}).get("before_install")) if isinstance(x.get("tips"), dict) else "",
        "en": {"title": pick(x.get("title"), EN) or folder, "tagline": pick(x.get("tagline"), EN),
               "description": pick(x.get("description"), EN),
               "tips": pick((x.get("tips") or {}).get("before_install"), EN) if isinstance(x.get("tips"), dict) else ""},
        "icon": str(x.get("icon") or ""),
        "thumbnail": str(x.get("thumbnail") or ""),
        "screenshots": [str(s) for s in shots][:6],
        "category": str(x.get("category") or "Övrigt"),
        "developer": str(x.get("developer") or x.get("author") or ""),
        "version": str(x.get("version") or ""),
        "website": str(x.get("website") or ""),
        "port_map": str(x.get("port_map") or ""),
        "scheme": str(x.get("scheme") or "http"),
        "index": str(x.get("index") or "/"),
        "architectures": archs,
        "main": main,
        "services": len(services),
        "compose": text,
        "wizzos": doc.get("x-wizzos") if isinstance(doc.get("x-wizzos"), dict) else {},
    }


def _github_parts(url: str):
    m = GH_ZIP.match(url.strip())
    if m:
        return m.group(1), m.group(2), m.group(3)
    m = GH_REPO.match(url.strip())
    if m:
        return m.group(1), m.group(2), m.group(3) or None
    return None


async def _fetch_github(client: httpx.AsyncClient, owner, repo, branch):
    if not branch:
        r = await client.get(f"https://api.github.com/repos/{owner}/{repo}")
        r.raise_for_status()
        branch = r.json()["default_branch"]
    r = await client.get(
        f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1")
    if r.status_code == 403:
        raise RuntimeError("GitHub begränsar just nu antalet hämtningar. Försök igen om en stund.")
    r.raise_for_status()
    paths = [t["path"] for t in r.json().get("tree", [])
             if re.search(r"(^|/)Apps/[^/]+/docker-compose\.ya?ml$", t["path"])]
    sem = asyncio.Semaphore(16)

    async def one(path):
        async with sem:
            raw = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
            try:
                resp = await client.get(raw)
                resp.raise_for_status()
                return path, resp.text
            except httpx.HTTPError:
                return path, None

    return await asyncio.gather(*(one(p) for p in paths))


async def _fetch_zip(client: httpx.AsyncClient, url: str):
    out = []
    with tempfile.TemporaryFile() as tmp:
        async with client.stream("GET", url) as r:
            r.raise_for_status()
            async for chunk in r.aiter_bytes():
                tmp.write(chunk)
        tmp.seek(0)
        with zipfile.ZipFile(tmp) as z:
            for n in z.namelist():
                if re.search(r"(^|/)Apps/[^/]+/docker-compose\.ya?ml$", n):
                    out.append((n, z.read(n).decode("utf-8", "replace")))
    return out


async def refresh(store: dict) -> dict:
    """Ladda ner en butik och spara den lokalt. Returnerar sammanfattning."""
    async with httpx.AsyncClient(headers=UA, timeout=60, follow_redirects=True) as client:
        gh = _github_parts(store["url"])
        files = await (_fetch_github(client, *gh) if gh else _fetch_zip(client, store["url"]))
    apps = []
    for path, text in files:
        if not text:
            continue
        folder = path.rstrip("/").split("/")[-2]
        app = parse_compose(text, store["id"], folder)
        if app:
            apps.append(app)
    apps.sort(key=lambda a: a["title"].lower())
    data = {"id": store["id"], "name": store["name"], "url": store["url"],
            "updated": int(time.time()), "apps": apps}
    config.ensure_dirs()
    store_file(store["id"]).write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    _cache[store["id"]] = data
    return {"id": store["id"], "count": len(apps), "updated": data["updated"]}


def load_store(store_id: str) -> dict | None:
    if store_id in _cache:
        return _cache[store_id]
    f = store_file(store_id)
    if f.exists():
        try:
            _cache[store_id] = json.loads(f.read_text("utf-8"))
            return _cache[store_id]
        except ValueError:
            return None
    return None


def forget(store_id: str) -> None:
    _cache.pop(store_id, None)
    store_file(store_id).unlink(missing_ok=True)


def all_apps(stores: list[dict]) -> list[dict]:
    from . import recipes
    out = recipes.all_apps()  # WizzOS egna paket först
    seen = {a["name"] for a in out}
    for s in stores:
        data = load_store(s["id"])
        if not data:
            continue
        for a in data["apps"]:
            # Samma app i flera butiker: visa den från första butiken.
            if a["name"] in seen:
                continue
            seen.add(a["name"])
            out.append(a)
    out.sort(key=lambda a: a["title"].lower())
    return out


def find(stores: list[dict], key: str) -> dict | None:
    store_id = key.split("/", 1)[0]
    if store_id == "wizzos":
        from . import recipes
        return recipes.find(key)
    data = load_store(store_id)
    if not data:
        return None
    return next((a for a in data["apps"] if a["key"] == key), None)


def summary(app: dict) -> dict:
    """Det som listan i butiken behöver (utan compose-texten)."""
    app = localized(app)
    return {k: app[k] for k in ("key", "store", "name", "title", "tagline", "icon",
                                "category", "developer", "architectures")}
