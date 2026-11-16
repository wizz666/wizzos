"""Inloggning: ägarens lösenord + familjekonton (app/users.py), en signerad cookie.

Cookien är "utgång.id.signatur". Signaturen täcker även personens
"generation", så ett nytt lösenord eller en borttagen person gör gamla
inloggningar ogiltiga direkt. Äldre cookies ("utgång.signatur") gäller ägaren.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from . import config

COOKIE = "wizzos_session"
SESSION_DAYS = 30

_failures: dict[str, list[float]] = {}


def _hash(password: str, salt: bytes) -> str:
    key = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return base64.b64encode(salt).decode() + "$" + base64.b64encode(key).decode()


def hash_password(password: str) -> str:
    return _hash(password, secrets.token_bytes(16))


def verify_hash(password: str, stored: str) -> bool:
    if "$" not in (stored or ""):
        return False
    salt = base64.b64decode(stored.split("$", 1)[0])
    return hmac.compare_digest(_hash(password, salt), stored)


def has_password() -> bool:
    return bool(config.load().get("password_hash"))


def set_password(password: str) -> None:
    # Nytt hemligt värde = alla gamla inloggningar slutar gälla (även familjens).
    config.update(password_hash=hash_password(password), secret=secrets.token_hex(32))


def check_password(password: str) -> bool:
    return verify_hash(password, config.load().get("password_hash", ""))


def too_many_failures(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _failures.get(ip, []) if now - t < 300]
    _failures[ip] = recent
    return len(recent) >= 8


def note_failure(ip: str) -> None:
    _failures.setdefault(ip, []).append(time.time())


def _sig(secret: str, expires: str, uid: str, gen: int) -> str:
    msg = expires if uid == "owner" and gen == 0 else f"{expires}.{uid}.{gen}"
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


def make_token(uid: str = "owner") -> str:
    from . import users
    u = users.get(uid)
    if not u:
        raise ValueError("Okänd person.")
    expires = str(int(time.time()) + SESSION_DAYS * 86400)
    return f"{expires}.{uid}.{_sig(config.load()['secret'], expires, uid, u.get('gen', 0))}"


def current(token: str | None) -> dict | None:
    """Personen som cookien hör till, eller None."""
    from . import users
    if not token or "." not in token:
        return None
    secret = config.load().get("secret", "")
    if not secret:
        return None
    parts = token.split(".")
    if len(parts) == 2:
        expires, uid, sig = parts[0], "owner", parts[1]
    elif len(parts) == 3:
        expires, uid, sig = parts
    else:
        return None
    if not expires.isdigit() or int(expires) <= time.time():
        return None
    u = users.get(uid)
    if not u or not hmac.compare_digest(sig, _sig(secret, expires, uid, u.get("gen", 0))):
        return None
    return u


def valid_token(token: str | None) -> bool:
    return current(token) is not None
