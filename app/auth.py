"""Inloggning: ett lösenord, en signerad cookie. Ingen användardatabas."""
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


def has_password() -> bool:
    return bool(config.load().get("password_hash"))


def set_password(password: str) -> None:
    # Nytt hemligt värde = alla gamla inloggningar slutar gälla.
    config.update(password_hash=_hash(password, secrets.token_bytes(16)),
                  secret=secrets.token_hex(32))


def check_password(password: str) -> bool:
    stored = config.load().get("password_hash", "")
    if "$" not in stored:
        return False
    salt = base64.b64decode(stored.split("$", 1)[0])
    return hmac.compare_digest(_hash(password, salt), stored)


def too_many_failures(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _failures.get(ip, []) if now - t < 300]
    _failures[ip] = recent
    return len(recent) >= 8


def note_failure(ip: str) -> None:
    _failures.setdefault(ip, []).append(time.time())


def make_token() -> str:
    secret = config.load()["secret"].encode()
    expires = str(int(time.time()) + SESSION_DAYS * 86400)
    sig = hmac.new(secret, expires.encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{sig}"


def valid_token(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    secret = config.load().get("secret", "")
    if not secret:
        return False
    expires, sig = token.split(".", 1)
    good = hmac.new(secret.encode(), expires.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, good) and expires.isdigit() and int(expires) > time.time()
