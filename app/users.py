"""Familjekonton: fler personer på samma server, med egna lösenord.

Roller
- admin  – samma som ägaren: får göra allt.
- family – ser bara de appar ägaren valt och har en egen mapp (/DATA/Hem/<namn>).
- guest  – ser bara de appar ägaren valt. Inga filer.

Ägaren själv (lösenordet som valdes vid första start) är inte med i listan –
hen har id "owner" och kan inte tas bort härifrån.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from . import config

FILE = config.HOME / "users.json"
ROLES = {"admin": "Admin", "family": "Familj", "guest": "Gäst"}
HOME_DIR = "Hem"
_lock = threading.Lock()


def _load() -> list[dict]:
    try:
        data = json.loads(FILE.read_text("utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(users: list[dict]) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(users, ensure_ascii=False, indent=2), "utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(FILE)


def owner() -> dict:
    s = config.load()
    return {"id": "owner", "name": s.get("owner_name") or "Ägaren", "role": "admin", "owner": True,
            "emoji": s.get("owner_emoji") or "👑", "gen": 0}


def get(uid: str) -> dict | None:
    if uid == "owner":
        return owner()
    return next((u for u in _load() if u["id"] == uid), None)


def _public(u: dict) -> dict:
    return {k: u.get(k) for k in ("id", "name", "role", "emoji", "apps", "folder", "created", "last_login", "owner")
            if k in u}


def listing() -> list[dict]:
    return [_public(owner())] + [_public(u) for u in _load()]


def login_choices() -> list[dict]:
    """Namnen som visas på inloggningssidan (bara om det finns fler än ägaren)."""
    users = _load()
    if not users:
        return []
    return [{"id": u["id"], "name": u["name"], "emoji": u.get("emoji", "")} for u in [owner()] + users]


def _slug(name: str) -> str:
    s = name.strip().lower()
    for a, b in (("å", "a"), ("ä", "a"), ("ö", "o"), ("é", "e"), ("ü", "u")):
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:30] or "person"


def _check(name: str, role: str) -> str:
    name = (name or "").strip()
    if not name or len(name) > 30 or any(c in name for c in "<>\"'/\\"):
        raise ValueError("Skriv ett namn (högst 30 tecken).")
    if role not in ROLES:
        raise ValueError("Okänd roll.")
    return name


def _make_folder(slug: str) -> str:
    d = config.DATA_ROOT / HOME_DIR / slug
    d.mkdir(parents=True, exist_ok=True)
    s = config.load()
    for p in (d.parent, d):
        try:
            os.chown(p, int(s["puid"]), int(s["pgid"]))
        except (OSError, ValueError, KeyError, AttributeError):
            pass
    return f"/{HOME_DIR}/{slug}"


def create(name: str, role: str, password: str, apps: list[str] | None = None, emoji: str = "") -> dict:
    from . import auth
    name = _check(name, role)
    if len(password or "") < 6:
        raise ValueError("Lösenordet behöver minst 6 tecken.")
    with _lock:
        users = _load()
        if any(u["name"].lower() == name.lower() for u in users) or name.lower() == owner()["name"].lower():
            raise ValueError("Det finns redan någon med det namnet.")
        slug, n = _slug(name), 2
        taken = {u.get("slug") for u in users}
        base = slug
        while slug in taken:
            slug, n = f"{base}-{n}", n + 1
        u = {"id": secrets.token_hex(6), "name": name, "role": role, "slug": slug,
             "emoji": (emoji or "")[:4], "apps": sorted(set(apps or [])),
             "hash": auth.hash_password(password), "gen": 1, "created": int(time.time())}
        if role == "family":
            u["folder"] = _make_folder(slug)
        users.append(u)
        _save(users)
    return _public(u)


def update(uid: str, name: str | None = None, role: str | None = None, apps: list[str] | None = None,
           emoji: str | None = None, password: str | None = None) -> dict:
    from . import auth
    if uid == "owner":
        s = {}
        if name is not None:
            s["owner_name"] = _check(name, "admin")
        if emoji is not None:
            s["owner_emoji"] = emoji[:4]
        if s:
            config.update(**s)
        return _public(owner())
    with _lock:
        users = _load()
        u = next((x for x in users if x["id"] == uid), None)
        if not u:
            raise ValueError("Personen finns inte längre.")
        if name is not None:
            n = _check(name, role or u["role"])
            if any(x["id"] != uid and x["name"].lower() == n.lower() for x in users):
                raise ValueError("Det finns redan någon med det namnet.")
            u["name"] = n
        if role is not None and role != u["role"]:
            _check(u["name"], role)
            u["role"] = role
            u["gen"] = u.get("gen", 1) + 1  # ny roll = logga in igen
            if role == "family" and not u.get("folder"):
                u["folder"] = _make_folder(u["slug"])
        if apps is not None:
            u["apps"] = sorted(set(apps))
        if emoji is not None:
            u["emoji"] = emoji[:4]
        if password:
            if len(password) < 6:
                raise ValueError("Lösenordet behöver minst 6 tecken.")
            u["hash"] = auth.hash_password(password)
            u["gen"] = u.get("gen", 1) + 1  # nytt lösenord = alla gamla inloggningar slutar gälla
        _save(users)
        return _public(u)


def remove(uid: str) -> None:
    """Tar bort kontot. Personens mapp ligger kvar (ägaren bestämmer själv över den)."""
    if uid == "owner":
        raise ValueError("Ägaren kan inte tas bort.")
    with _lock:
        users = _load()
        if not any(u["id"] == uid for u in users):
            raise ValueError("Personen finns inte längre.")
        _save([u for u in users if u["id"] != uid])


def check_password(uid: str, password: str) -> bool:
    from . import auth
    u = next((x for x in _load() if x["id"] == uid), None)
    return bool(u) and auth.verify_hash(password, u.get("hash", ""))


def note_login(uid: str) -> None:
    if uid == "owner":
        return
    with _lock:
        users = _load()
        for u in users:
            if u["id"] == uid:
                u["last_login"] = int(time.time())
        _save(users)


def folder_path(u: dict) -> Path | None:
    if u.get("role") != "family" or not u.get("folder"):
        return None
    p = (config.DATA_ROOT / u["folder"].lstrip("/"))
    p.mkdir(parents=True, exist_ok=True)
    return p.resolve()


def visible_apps(u: dict) -> list[dict]:
    """De appar personen får se – bara det som behövs för att öppna dem."""
    from . import apps as apps_mod
    allowed = None if u.get("role") == "admin" else set(u.get("apps") or [])
    out = []
    for a in apps_mod.installed()["apps"]:
        if allowed is not None and a["name"] not in allowed:
            continue
        out.append({k: a.get(k) for k in ("name", "title", "icon", "port", "scheme", "index", "url", "status", "tagline")})
    return out
