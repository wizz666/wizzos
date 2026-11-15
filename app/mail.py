"""E-post via SMTP – används av "Om jag försvinner" (påminnelser och brevet till
de du litar på). Lösenordet sparas bara på servern och visas aldrig igen."""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from . import config

DEFAULTS = {"host": "", "port": 587, "user": "", "password": "", "sender": "", "security": "starttls"}


def cfg() -> dict:
    return {**DEFAULTS, **(config.load().get("mail") or {})}


def configured() -> bool:
    c = cfg()
    return bool(c["host"] and c["sender"])


def public_cfg() -> dict:
    c = cfg()
    return {k: v for k, v in c.items() if k != "password"} | {"has_password": bool(c["password"]),
                                                               "configured": configured()}


def save(ch: dict) -> dict:
    c = cfg()
    for k in ("host", "user", "sender"):
        if ch.get(k) is not None:
            c[k] = str(ch[k]).strip()
    if ch.get("port"):
        c["port"] = int(ch["port"])
    if ch.get("security") in ("starttls", "ssl", "none"):
        c["security"] = ch["security"]
    if ch.get("password"):
        c["password"] = ch["password"].strip()
    if not c["host"]:
        c = dict(DEFAULTS)
    if c["sender"] and "@" not in c["sender"]:
        raise ValueError("Avsändaren ska vara en e-postadress.")
    config.update(mail=c)
    return public_cfg()


def send(to: str | list[str], subject: str, text: str, reply_to: str = "") -> None:
    c = cfg()
    if not configured():
        raise ValueError("E-post är inte inställd (Inställningar → E-post).")
    to = [to] if isinstance(to, str) else to
    msg = EmailMessage()
    from . import alerts
    msg["From"] = formataddr((f"WizzOS ({alerts._server_name()})", c["sender"]))
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=c["sender"].split("@")[-1])
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(text)
    ctx = ssl.create_default_context()
    try:
        if c["security"] == "ssl":
            s = smtplib.SMTP_SSL(c["host"], c["port"] or 465, timeout=30, context=ctx)
        else:
            s = smtplib.SMTP(c["host"], c["port"] or 587, timeout=30)
            if c["security"] == "starttls":
                s.starttls(context=ctx)
        with s:
            if c["user"]:
                s.login(c["user"], c["password"])
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise ValueError("E-postservern godkände inte användarnamn/lösenord. "
                         "Gmail kräver ett app-lösenord (inte ditt vanliga).")
    except (smtplib.SMTPException, OSError) as e:
        raise ValueError(f"Kunde inte skicka e-post: {e}")
