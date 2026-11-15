"""Säkerhetskoll i klartext.

Går igenom servern och apparna och säger på vanlig svenska vad som är bra,
vad som bör fixas och hur. Ger ett betyg 0–100. Allt är skrivskyddat – koll,
inga ändringar. Där det går finns en knapp som leder rätt.
"""
from __future__ import annotations

import glob
import ipaddress
import json
import re
import time
from pathlib import Path

import yaml

from . import apps, config

WEAK = {"password", "passw0rd", "changeme", "change_me", "admin", "123456", "12345678", "secret", "root",
        "casaos", "default", "test", "qwerty", "letmein", "example", "pass", "lösenord", "1234"}
_cache = {"t": 0, "v": None}


def _host(path: str) -> Path:
    return config.HOST_ROOT / path.lstrip("/")


def host_ips() -> list[str]:
    """Värdens egna IPv4-adresser (läses från värdens nätverk via /host/proc/1)."""
    try:
        txt = _host("/proc/1/net/fib_trie").read_text()
    except OSError:
        return []
    ips, prev = set(), ""
    for line in txt.splitlines():
        if "/32 host LOCAL" in line and prev:
            ips.add(prev)
        m = re.search(r"\|-- ([0-9.]+)$", line)
        prev = m.group(1) if m else ""
    return sorted(ips)


def public_ips() -> list[str]:
    out = []
    for ip in host_ips():
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if a.is_global and not ipaddress.ip_address(ip) in ipaddress.ip_network("100.64.0.0/10"):
            out.append(ip)
    return out


def _sshd() -> dict:
    """Gällande värden (först satt vinner, som sshd själv gör)."""
    seen: dict[str, str] = {}

    def read(p: Path):
        try:
            lines = p.read_text("utf-8", "replace").splitlines()
        except OSError:
            return
        for raw in lines:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split(None, 1)
            key = parts[0].lower()
            val = parts[1].strip() if len(parts) > 1 else ""
            if key == "match":
                return  # resten gäller bara vissa användare
            if key == "include":
                for pat in val.split():
                    pat = pat if pat.startswith("/") else "/etc/ssh/" + pat
                    for f in sorted(glob.glob(str(_host(pat)))):
                        read(Path(f))
                continue
            seen.setdefault(key, val.lower())

    main = _host("/etc/ssh/sshd_config")
    if not main.exists():
        return {}
    read(main)
    return seen


def _published(c_ports: str) -> list[tuple[str, str]]:
    """[(ip, port)] från docker ps-kolumnen Ports."""
    return re.findall(r"(\[?[0-9a-f.:]*\]?):(\d+)(?:-\d+)?->", c_ports)


def check() -> dict:
    items: list[dict] = []

    def add(level, title, text, link="", link_text=""):
        items.append({"level": level, "title": title, "text": text, "link": link, "link_text": link_text})

    pubs = public_ips()
    inst = apps.installed()
    mine = inst["apps"]

    # 1. Appar som syns mot hela internet
    try:
        rows = [json.loads(x) for x in apps.docker("ps", "--format", "{{json .}}", timeout=20).splitlines() if x.strip()]
    except Exception:  # noqa: BLE001
        rows = []
    open_apps = []
    for r in rows:
        for ip, port in _published(r.get("Ports", "")):
            ip = ip.strip("[]")
            if ip in ("0.0.0.0", "::", "") or ip in pubs:
                open_apps.append((r.get("Names", ""), port))
    if pubs and open_apps:
        names = sorted({f"{n} (port {p})" for n, p in open_apps})
        add("bad", f"{len(names)} sak{'er' if len(names) > 1 else ''} syns för hela internet",
            f"Servern har en publik adress ({', '.join(pubs)}) och de här nås av vem som helst som hittar dem: "
            f"{', '.join(names[:6])}{' …' if len(names) > 6 else ''}. Bots letar hela tiden efter sådant. "
            "Låt apparna bara lyssna på din Tailscale-adress (Inställningar → Var apparna nås), "
            "eller ge dem en egen adress med https och inloggning.", "#/settings", "Gå till inställningar")
    elif pubs:
        add("ok", "Inga appar syns för hela internet",
            "Servern har en publik adress, men apparna lyssnar bara på privata adresser. Bra!")
    else:
        add("ok", "Servern står bakom din router", "Den har ingen publik adress, så apparna syns inte på internet.")

    # 2. Standardlösenord i apparnas inställningar
    weak_apps = []
    for a in mine:
        try:
            doc = yaml.safe_load((apps.app_dir(a["name"]) / "docker-compose.yml").read_text("utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        for svc in (doc.get("services") or {}).values():
            env = svc.get("environment") or {}
            if isinstance(env, list):
                env = dict(e.split("=", 1) for e in env if isinstance(e, str) and "=" in e)
            for k, v in env.items():
                if re.search(r"pass|pwd|secret|token", str(k), re.I) and str(v).strip().lower() in WEAK:
                    weak_apps.append((a["title"], k))
    if weak_apps:
        add("bad", "Standardlösenord i " + ", ".join(sorted({t for t, _ in weak_apps})),
            "Inställningen " + ", ".join(sorted({k for _, k in weak_apps})) + " har ett lösenord som står i "
            "instruktionerna på nätet (t.ex. \"changeme\"). Byt det i appens meny → Redigera inställningar.")
    else:
        add("ok", "Inga kända standardlösenord", "Inga appar använder lösenord som \"admin\" eller \"changeme\".")

    # 3. Appar med full makt över servern
    risky = []
    for r in rows:
        name = r.get("Names", "")
        if name in ("wizzos", "wizzos-proxy"):
            continue
        try:
            info = json.loads(apps.docker("inspect", "--format", "{{json .HostConfig}}", name, timeout=15))
        except Exception:  # noqa: BLE001
            continue
        why = []
        if info.get("Privileged"):
            why.append("körs med full behörighet (privileged)")
        if any("docker.sock" in b for b in (info.get("Binds") or [])):
            why.append("kan styra Docker")
        if info.get("NetworkMode") == "host" and why:
            why.append("delar serverns nätverk")
        if why:
            risky.append(f"{name}: {', '.join(why)}")
    if risky:
        add("warn", f"{len(risky)} app{'ar' if len(risky) > 1 else ''} har full makt över servern",
            "Om någon tar sig in i dem kan de ta över hela servern: " + "; ".join(risky[:5]) +
            ". Det är ibland nödvändigt (t.ex. för övervakning), men installera bara sådant du litar på.")

    # 4. SSH
    ssh = _sshd()
    if ssh:
        root = ssh.get("permitrootlogin", "prohibit-password")
        pw = ssh.get("passwordauthentication", "yes")
        if root == "yes" and pw == "yes":
            add("bad", "Root kan logga in med lösenord via SSH",
                "Det är det som bots provar mest. Använd nycklar i stället och sätt PasswordAuthentication no "
                "och PermitRootLogin prohibit-password i /etc/ssh/sshd_config.")
        elif pw == "yes":
            add("warn", "SSH tillåter inloggning med lösenord",
                "Nycklar är mycket säkrare. Sätt PasswordAuthentication no när du har lagt in din nyckel.")
        else:
            add("ok", "SSH kräver nyckel", "Ingen kan gissa sig in med lösenord.")

    # 5. Säkerhetsuppdateringar som väntar på omstart
    if _host("/run/reboot-required").exists():
        try:
            pkgs = sorted(set(_host("/run/reboot-required.pkgs").read_text().split()))
        except OSError:
            pkgs = []
        add("warn", "Servern behöver startas om",
            "Säkerhetsuppdateringar är installerade men gäller inte förrän datorn startats om"
            + (f" ({', '.join(pkgs[:5])})." if pkgs else ".") + " Starta om när det passar – apparna startar själva igen.")

    # 6. Backup
    from . import backup
    b = backup.summary()
    if not b["configured"]:
        add("bad", "Ingen backup", "Om disken går sönder eller servern blir hackad är allt borta. "
            "Det tar två minuter att ställa in.", "#/backup", "Ställ in backup")
    elif not b.get("last_test"):
        add("warn", "Backupen är inte provåterställd än", "Den körs, men ingen har bevisat att den går att läsa tillbaka. "
            "WizzOS gör det automatiskt efter några körningar.", "#/backup", "Till backup")
    elif not b["last_test"]["ok"]:
        add("bad", "Senaste provåterställningen misslyckades", "Backupen kanske inte går att använda.", "#/backup", "Till backup")
    else:
        add("ok", "Backupen är bevisad", "Den har lästs tillbaka och kontrollerats.")

    # 7. Uppdateringar
    from . import updater
    u = updater.cfg()
    if not u.get("enabled"):
        add("warn", "Nattliga uppdateringar är avstängda",
            "Säkerhetshål lagas i nya versioner. Slå på dem – WizzOS backar själv om något går fel.",
            "#/settings", "Slå på")
    av = len(updater.state().get("available") or {})
    if av:
        add("warn", f"{av} app{'ar' if av > 1 else ''} har nya versioner", "Nya versioner innehåller ofta säkerhetsfixar.",
            "#/settings", "Uppdatera")

    # 8. Larm
    from . import alerts
    if not alerts.cfg().get("enabled"):
        add("warn", "Du får inga larm", "Om något går fel märker du det inte förrän du själv tittar. "
            "Koppla larm till mobilen.", "#/settings", "Slå på larm")

    # 9. Inloggningsförsök
    from . import auth
    fails = sum(len([t for t in v if time.time() - t < 86400]) for v in auth._failures.values())
    if fails >= 20:
        add("warn", f"{fails} misslyckade inloggningar senaste dygnet",
            "Någon kan försöka gissa ditt lösenord. Se till att det är långt och unikt.")

    # 10. https
    from . import proxy
    if not proxy.wizz_url() and pubs:
        add("warn", "WizzOS nås utan https", "Lösenordet skickas okrypterat om du loggar in över internet. "
            "Ge WizzOS en egen adress med https, eller använd bara Tailscale.", "#/settings", "Egen adress")

    # 11. Delningar
    from . import shares
    act = shares.active()
    if act:
        never = [s for s in act if not s["expires"]]
        add("warn" if never else "ok", f"{len(act)} aktiv{'a' if len(act) > 1 else ''} delning{'ar' if len(act) > 1 else ''}",
            "Länkar som andra kan öppna utan inloggning." + (" Någon av dem går aldrig ut." if never else
            " Alla slutar fungera av sig själva."), "#/trygghet", "Visa delningar")

    bad = sum(1 for i in items if i["level"] == "bad")
    warn = sum(1 for i in items if i["level"] == "warn")
    score = max(0, 100 - 22 * bad - 8 * warn)
    grade = ("Mycket bra" if score >= 90 else "Bra, men fixa det gula" if score >= 75
             else "Behöver åtgärdas" if score >= 50 else "Osäker – fixa det röda först")
    order = {"bad": 0, "warn": 1, "ok": 2}
    items.sort(key=lambda i: order[i["level"]])
    res = {"score": score, "grade": grade, "items": items, "bad": bad, "warn": warn, "time": time.time()}
    _cache.update(t=time.time(), v=res)
    return res


def cached(max_age: int = 600) -> dict:
    if _cache["v"] and time.time() - _cache["t"] < max_age:
        return _cache["v"]
    return check()
