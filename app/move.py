"""Flytta servern: allt till en ny dator på några minuter.

Gamla servern: gör en färsk backup och skapar en *flyttkod* – inställningarna
och backupens nycklar, krypterade med ett flyttlösenord du väljer
(scrypt + AES-GCM). Koden är värdelös utan lösenordet.

Nya servern: klistra in koden och lösenordet. WizzOS tar över inställningarna,
ansluter till samma backup och återställer varje app med sin data, sina
adresser och exakt samma programversioner.

Det som hör till själva maskinen följer inte med: inloggningslösenordet,
vem som når apparna (bind_ip) och vilken IP den egna adressen pekar på.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import apps, backup, config, jobs, proxy

PREFIX = "WZMOVE1."
# Inställningar som följer med (allt annat hör till maskinen).
CARRY = ("server_name", "tz", "puid", "pgid", "stores", "undo_max_gb",
         "alerts", "ai", "nestward", "cf_token", "proxy_domain")


def _key(passphrase: str, salt: bytes) -> bytes:
    import hashlib
    return hashlib.scrypt(passphrase.encode(), salt=salt, n=2 ** 15, r=8, p=1,
                          maxmem=64 * 1024 * 1024, dklen=32)


def _check_pass(passphrase: str) -> str:
    p = (passphrase or "").strip()
    if len(p) < 10:
        raise ValueError("Flyttlösenordet behöver minst 10 tecken.")
    return p


def make_code(passphrase: str) -> str:
    p = _check_pass(passphrase)
    s = config.load()
    bk = backup.cfg()
    if not (bk.get("type") and bk.get("password")):
        raise ValueError("Ställ in backup först – flytten går via backupen.")
    from . import system
    bundle = {"v": 1, "made": int(time.time()), "from": s.get("server_name") or system.hostname(),
              "settings": {k: s[k] for k in CARRY if k in s},
              "proxy_was_on": bool(s.get("proxy_enabled")),
              "backup": {k: v for k, v in bk.items() if k not in ("configured_at",)}}
    keyfile = config.HOME / "backup" / "id_ed25519"
    if bk.get("type") == "sftp" and keyfile.exists():
        bundle["sftp_key"] = keyfile.read_text("utf-8")
    raw = json.dumps(bundle, ensure_ascii=False).encode()
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    ct = AESGCM(_key(p, salt)).encrypt(nonce, raw, PREFIX.encode())
    return PREFIX + base64.urlsafe_b64encode(salt + nonce + ct).decode().rstrip("=")


def read_code(code: str, passphrase: str) -> dict:
    code = "".join((code or "").split())
    if not code.startswith(PREFIX):
        raise ValueError("Det där ser inte ut som en flyttkod (den börjar med WZMOVE1.).")
    try:
        blob = base64.urlsafe_b64decode(code[len(PREFIX):] + "=" * (-len(code[len(PREFIX):]) % 4))
        salt, nonce, ct = blob[:16], blob[16:28], blob[28:]
        raw = AESGCM(_key(_check_pass(passphrase), salt)).decrypt(nonce, ct, PREFIX.encode())
    except ValueError:
        raise
    except Exception:  # noqa: BLE001
        raise ValueError("Fel flyttlösenord, eller så är koden inte hel.")
    return json.loads(raw)


def export_job(passphrase: str) -> str:
    _check_pass(passphrase)

    def work(log):
        log("Gör en färsk backup av alla appar först ...")
        res = backup.backup_all(log)
        if res.get("failed"):
            log(f"Obs: backupen misslyckades för {', '.join(res['failed'])}. De följer med i det skick de hade vid förra backupen.")
        code = make_code(passphrase)
        log("Flyttkoden är klar. Spara den tillsammans med flyttlösenordet.")
        return {"code": code}
    return jobs.start("Förbereder flytt", work)


def preview(code: str, passphrase: str) -> dict:
    b = read_code(code, passphrase)
    return {"from": b.get("from", ""), "made": b.get("made"), "target": backup.describe_target(b["backup"]),
            "proxy_domain": b["settings"].get("proxy_domain", ""), "proxy_was_on": b.get("proxy_was_on")}


def import_job(code: str, passphrase: str) -> str:
    b = read_code(code, passphrase)  # fel lösenord märks direkt, inte i jobbet

    def work(log):
        log(f"Flyttar från {b.get('from') or 'den gamla servern'} ...")
        s = b["settings"]
        config.update(**s)
        log("Inställningar övertagna: namn, tidszon, appbutiker, larm, AI-hjälp, Nestward.")
        if b.get("sftp_key"):
            kd = config.HOME / "backup"
            kd.mkdir(parents=True, exist_ok=True)
            kf = kd / "id_ed25519"
            kf.write_text(b["sftp_key"], "utf-8")
            os.chmod(kf, 0o600)
        bk = dict(b["backup"])
        bk["existing_password"] = bk.pop("password")
        backup.setup(bk, log)
        avail = backup.apps_in_backup()
        log(f"Hittade {len(avail)} appar i backupen.")
        ok, failed = [], []
        for a in avail:
            if a["installed"]:
                log(f"{a['name']} finns redan här – hoppar över.")
                continue
            log(f"—— Återställer {a['name']} ——")
            try:
                backup.restore_app(a["name"], a["snapshot"], log)
                good, why = apps.wait_healthy(a["name"], log, stable=10)
                (ok if good else failed).append(a["name"])
                if not good:
                    log(f"{a['name']} är tillbaka men mår inte bra: {why}")
            except Exception as e:  # noqa: BLE001
                log(f"{a['name']} kunde inte återställas: {e}")
                failed.append(a["name"])
        if s.get("proxy_domain") and b.get("proxy_was_on"):
            log(f"Egen adress: {s['proxy_domain']} är förifylld. Slå på den under Inställningar → Egen adress "
                f"när du vill att adresserna ska peka hit (förslag på IP: {proxy.suggested_ip() or 'serverns Tailscale-IP'}).")
        log(f"Klart! {len(ok)} appar flyttade" + (f", {len(failed)} med problem: {', '.join(failed)}" if failed else "") + ".")
        return {"ok": ok, "failed": failed}
    return jobs.start("Flyttar hit servern", work)
