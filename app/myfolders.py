"""Egna mappar med några klick: namn → nåbar från datorn/telefonen → i backupen → vem får se.

Nätverksdelningen sköts av en liten Samba-behållare (wizzos-smb, image crazymax/samba)
som WizzOS skriver inställningarna till. Varje person har ett eget *nätverkslösenord*
(skilt från WizzOS-lösenordet – Windows/Mac sparar det i sin nyckelring).
Filer som skrivs över nätverket ägs av samma användare som resten av /DATA.
"""
from __future__ import annotations

import os
import re
import secrets
import subprocess
from pathlib import Path

import yaml

from . import apps, config

SMB_DIR = config.HOME / "smb"
SMB_IMAGE = "crazymax/samba:4.21.4"
SMB_PROJECT = "wizzos-smb"
RESERVED = {"wizzos", "appdata", "backup", "hem", "lost+found", "media", "downloads"}
_NAME_RE = re.compile(r"^[\wåäöÅÄÖ][\wåäöÅÄÖ .\-]{0,39}$")


def listing() -> list[dict]:
    return list(config.load().get("my_folders") or [])


def _save(rows: list[dict]) -> None:
    config.update(my_folders=rows)


def _smb_name(u: dict) -> str:
    base = re.sub(r"[^a-z0-9]", "", (u.get("slug") or u.get("name") or "").lower()
                  .replace("å", "a").replace("ä", "a").replace("ö", "o"))
    return (base or ("agare" if u["id"] == "owner" else "person"))[:20]


def people() -> list[dict]:
    from . import users
    pw = config.load().get("smb_passwords") or {}
    out = []
    for u in [users.owner(), *users._load()]:
        if u["id"] != "owner" and u.get("role") == "guest":
            continue
        out.append({"id": u["id"], "name": u["name"], "smb_user": _smb_name(u), "has_password": u["id"] in pw})
    return out


def set_password(uid: str, password: str) -> None:
    if len(password or "") < 8:
        raise ValueError("Nätverkslösenordet behöver minst 8 tecken.")
    if any(c in password for c in "\n\r\"'\\"):
        raise ValueError("Använd inte citattecken eller snedstreck i nätverkslösenordet.")
    pw = dict(config.load().get("smb_passwords") or {})
    pw[uid] = password
    config.update(smb_passwords=pw)
    apply()


def create(name: str, network: bool = True, backup_on: bool = True, who: str = "me") -> dict:
    name = (name or "").strip()
    if not _NAME_RE.match(name) or name.lower() in RESERVED or name.startswith("."):
        raise ValueError("Välj ett enkelt namn – bokstäver, siffror, mellanslag eller bindestreck (max 40 tecken).")
    if who not in ("me", "family"):
        raise ValueError("Okänt val för vem som får se mappen.")
    path = Path(config.DATA_ROOT) / name
    if any(r["path"] == str(path) for r in listing()):
        raise ValueError("Den mappen finns redan.")
    path.mkdir(parents=True, exist_ok=True)
    s = config.load()
    try:
        os.chown(path, int(s["puid"]), int(s["pgid"]))
    except (OSError, KeyError, ValueError):
        pass
    row = {"name": name, "path": str(path), "network": bool(network), "who": who}
    _save([*listing(), row])
    if backup_on:
        from . import safety
        safety.add_folder(str(path), name)
    if network:
        apply()
    return row


def update(path: str, network: bool | None = None, who: str | None = None) -> dict:
    rows = listing()
    row = next((r for r in rows if r["path"] == path), None)
    if not row:
        raise ValueError("Mappen finns inte i listan.")
    if network is not None:
        row["network"] = bool(network)
    if who in ("me", "family"):
        row["who"] = who
    _save(rows)
    apply()
    return row


def forget(path: str) -> None:
    """Ta bort mappen ur listan (och nätverket). Själva filerna rörs inte."""
    _save([r for r in listing() if r["path"] != path])
    apply()


# ---------- Samba ----------

def _render() -> dict | None:
    s = config.load()
    pw = s.get("smb_passwords") or {}
    shares = [r for r in listing() if r.get("network") and os.path.isdir(r["path"])]
    if not shares or "owner" not in pw:
        return None
    ppl = [p for p in people() if p["id"] in pw]
    owner_user = next(p["smb_user"] for p in ppl if p["id"] == "owner")
    family_users = [p["smb_user"] for p in ppl if p["id"] != "owner"]
    auth = []
    for i, p in enumerate(ppl):
        auth.append({"user": p["smb_user"], "group": p["smb_user"], "uid": 1100 + i, "gid": 1100 + i,
                     "password": pw[p["id"]]})
    share = []
    for r in shares:
        valid = [owner_user] + (family_users if r.get("who") == "family" else [])
        share.append({"name": r["name"], "path": f"/samba/{r['name']}", "browsable": "yes", "readonly": "no",
                      "guestok": "no", "validusers": ", ".join(valid), "writelist": ", ".join(valid), "veto": "no"})
    return {"auth": auth, "global": [f"force user = {owner_user}", "server min protocol = SMB2_10",
                                    "map to guest = never", "load printers = no"],
            "share": share, "_owner": owner_user, "_shares": shares}


def _compose(doc: dict) -> str:
    s = config.load()
    bind = s.get("bind_ip") or ""
    port = f"{bind}:445:445" if bind and bind != "0.0.0.0" else "445:445"
    vols = [f"{SMB_DIR}:/data"] + [f"{r['path']}:/samba/{r['name']}" for r in doc["_shares"]]
    svc = {"image": SMB_IMAGE, "container_name": SMB_PROJECT, "restart": "unless-stopped",
           "ports": [port], "volumes": vols,
           "environment": {"TZ": s.get("tz", "Europe/Stockholm"), "SAMBA_LOG_LEVEL": "0"},
           "labels": {"wizzos.system": "smb"}}
    return yaml.safe_dump({"name": SMB_PROJECT, "services": {"samba": svc}}, sort_keys=False, allow_unicode=True)


def apply(log=None) -> dict:
    """Skriv Sambas inställningar och starta/stoppa behållaren."""
    doc = _render()
    SMB_DIR.mkdir(parents=True, exist_ok=True)
    cf = SMB_DIR / "docker-compose.yml"
    if doc is None:
        if cf.exists():
            subprocess.run(["docker", "compose", "-f", str(cf), "down"], capture_output=True)
        return {"running": False}
    owner_user = doc.pop("_owner")
    shares = doc.pop("_shares")
    s = config.load()
    # force user måste ha samma uid som resten av /DATA
    for a in doc["auth"]:
        if a["user"] == owner_user:
            a["uid"], a["gid"] = int(s["puid"]), int(s["pgid"])
    conf = SMB_DIR / "config.yml"
    conf.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), "utf-8")
    os.chmod(conf, 0o600)
    cf.write_text(_compose({"_shares": shares}), "utf-8")
    r = subprocess.run(["docker", "compose", "-f", str(cf), "up", "-d", "--force-recreate", "--pull", "missing"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-400:] or "Nätverksdelningen startade inte.")
    return {"running": True}


def addresses() -> dict:
    from . import system
    s = config.load()
    host = s.get("bind_ip") if s.get("bind_ip") not in (None, "", "0.0.0.0") else system.hostname()
    return {"host": host, "windows": f"\\\\{host}", "mac": f"smb://{host}"}


def summary() -> dict:
    running = False
    try:
        running = "running" in apps.docker("inspect", "-f", "{{.State.Status}}", SMB_PROJECT, timeout=10)
    except Exception:  # noqa: BLE001
        pass
    from . import safety
    in_backup = {f["path"] for f in safety.folders()}
    rows = [{**r, "backup": r["path"] in in_backup} for r in listing()]
    return {"folders": rows, "people": people(), "running": running, "address": addresses()}


def new_password() -> str:
    return secrets.token_urlsafe(9)
