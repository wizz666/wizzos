"""Egen adress med https: app.din-domän → rätt app.

- Cloudflare: WizzOS skapar två DNS-poster (domänen + *.domänen) som pekar
  på serverns IP. Är det en Tailscale- eller hemma-IP blir adresserna privata.
- Caddy (containern wizzos-proxy) hämtar ett certifikat för *.domänen från
  Let's Encrypt via Cloudflare-DNS och skickar vidare till apparna.
"""
from __future__ import annotations

import glob
import os
import re
import time

import httpx

from . import config

PROXY_DIR = config.HOME / "proxy"
CADDYFILE = PROXY_DIR / "Caddyfile"
SOCKET = PROXY_DIR / "admin.sock"
CF = "https://api.cloudflare.com/client/v4"
DOMAIN_RE = re.compile(r"^(?=.{4,200}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")


def settings() -> dict:
    s = config.load()
    return {"enabled": bool(s.get("proxy_enabled")), "domain": s.get("proxy_domain", ""),
            "ip": s.get("proxy_ip", ""), "has_token": bool(s.get("cf_token")),
            "suggested_ip": suggested_ip(), "status": s.get("proxy_status", "")}


def listen_ip() -> str:
    return os.environ.get("WIZZOS_LISTEN_IP", "0.0.0.0")


def suggested_ip() -> str:
    """VPS-läge: Tailscale-IP:n som WizzOS lyssnar på. Hemma: tomt (fylls i
    gränssnittet med adressen du själv använde för att öppna WizzOS)."""
    ip = listen_ip()
    return ip if ip not in ("0.0.0.0", "127.0.0.1", "") else ""


SUB_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
# Butikernas namnprefix som bara gör adressen längre.
PREFIXES = ("linuxserver-", "lsio-", "big-bear-", "bigbear-", "casaos-")


def default_sub(name: str) -> str:
    n = re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")
    for p in PREFIXES:
        if n.startswith(p) and len(n) > len(p):
            n = n[len(p):]
            break
    return n[:63].strip("-") or "app"


def sub_for(meta: dict) -> str:
    """Appens adressnamn: det du valt själv, annars ett kortat appnamn."""
    return meta.get("sub") or default_sub(meta["name"])


def app_url(meta: dict, sub: str | None = None) -> str:
    s = config.load()
    if not s.get("proxy_enabled") or not meta.get("port"):
        return ""
    idx = meta.get("index") or "/"
    if not idx.startswith("/"):
        idx = "/" + idx
    return f"https://{sub or sub_for(meta)}.{s['proxy_domain']}{idx}"


def assign_addresses(metas: list[dict]) -> None:
    """Sätt meta['address'] och meta['url'] för alla appar på en gång, så att
    två appar aldrig får samma adress. Egna val går före automatiska."""
    used = {m["sub"] for m in metas if m.get("sub") and m.get("port")}
    for m in metas:
        if not m.get("port"):
            m["address"], m["url"] = "", ""
            continue
        sub = m.get("sub")
        if not sub:
            sub = default_sub(m["name"])
            if sub in used:
                sub = re.sub(r"[^a-z0-9-]", "-", m["name"])[:63].strip("-")
            n = 2
            base = sub
            while sub in used:
                sub = f"{base[:60]}-{n}"
                n += 1
        used.add(sub)
        m["address"] = sub
        m["url"] = app_url(m, sub)


def wizz_url() -> str:
    s = config.load()
    return f"https://{s['proxy_domain']}" if s.get("proxy_enabled") else ""


# ---------- Cloudflare ----------

def _cf(token: str):
    return httpx.Client(base_url=CF, timeout=30,
                        headers={"Authorization": f"Bearer {token}"})


def _cf_ok(r: httpx.Response) -> dict:
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(f"Cloudflare svarade konstigt ({r.status_code}).")
    if not data.get("success"):
        errs = "; ".join(e.get("message", "") for e in data.get("errors", [])) or r.status_code
        raise RuntimeError(f"Cloudflare: {errs}")
    return data


def find_zone(client, domain: str) -> tuple[str, str]:
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        name = ".".join(parts[i:])
        data = _cf_ok(client.get("/zones", params={"name": name}))
        if data["result"]:
            return data["result"][0]["id"], name
    raise RuntimeError("Nyckeln kommer inte åt någon zon för den domänen hos Cloudflare.")


def upsert_dns(token: str, domain: str, ip: str, log) -> None:
    rtype = "AAAA" if ":" in ip else "A"
    with _cf(token) as c:
        zone_id, zone = find_zone(c, domain)
        log(f"Hittade zonen {zone} hos Cloudflare.")
        for name in (domain, f"*.{domain}"):
            existing = _cf_ok(c.get(f"/zones/{zone_id}/dns_records",
                                    params={"name": name}))["result"]
            body = {"type": rtype, "name": name, "content": ip, "ttl": 300, "proxied": False,
                    "comment": "WizzOS: egen adress till appar (privat IP)"}
            same = [r for r in existing if r["type"] == rtype]
            others = [r for r in existing if r["type"] in ("CNAME",) or (r["type"] in ("A", "AAAA") and r["type"] != rtype)]
            if others:
                raise RuntimeError(f"{name} har redan en annan post ({others[0]['type']}). Ta bort den först.")
            if same:
                _cf_ok(c.put(f"/zones/{zone_id}/dns_records/{same[0]['id']}", json=body))
                log(f"Uppdaterade DNS: {name} → {ip}")
            else:
                _cf_ok(c.post(f"/zones/{zone_id}/dns_records", json=body))
                log(f"Skapade DNS: {name} → {ip}")


def verify_token(token: str) -> None:
    with _cf(token) as c:
        data = _cf_ok(c.get("/user/tokens/verify"))
        if data["result"].get("status") != "active":
            raise RuntimeError("Cloudflare-nyckeln är inte aktiv.")


# ---------- Caddy ----------

def _dial_host() -> str:
    s = config.load()
    return s.get("bind_ip") or "127.0.0.1"


def render() -> str:
    s = config.load()
    lines = ["{", "\tadmin unix//proxy/admin.sock"]
    if not s.get("proxy_enabled"):
        lines.append("}")
        return "\n".join(lines) + "\n"
    domain, token = s["proxy_domain"], s["cf_token"]
    bind = listen_ip()
    lines += ["}", "", f"*.{domain}, {domain} {{"]
    if bind not in ("0.0.0.0", ""):
        lines.append(f"\tbind {bind}")
    lines += ["\ttls {", f"\t\tdns cloudflare {token}", "\t\tresolvers 1.1.1.1", "\t}",
              "\tencode gzip", ""]
    # WizzOS själv på huvudadressen
    wizz_host = bind if bind not in ("0.0.0.0", "") else "127.0.0.1"
    lines += [f"\t@wizzos host {domain}",
              f"\thandle @wizzos {{\n\t\treverse_proxy {wizz_host}:{os.environ.get('WIZZOS_PORT', '8899')}\n\t}}"]
    from . import apps
    dial = _dial_host()
    for meta in apps.installed()["apps"]:
        if not meta.get("address"):
            continue
        sub = meta["address"]
        target = f"{dial}:{meta['port']}"
        lines.append(f"\t@a_{sub.replace('-', '_')} host {sub}.{domain}")
        if meta.get("scheme") == "https":
            lines.append(f"\thandle @a_{sub.replace('-', '_')} {{\n\t\treverse_proxy https://{target} {{\n"
                         f"\t\t\ttransport http {{\n\t\t\t\ttls_insecure_skip_verify\n\t\t\t}}\n\t\t}}\n\t}}")
        else:
            lines.append(f"\thandle @a_{sub.replace('-', '_')} {{\n\t\treverse_proxy {target}\n\t}}")
    lines += ["\thandle {", '\t\trespond "Det finns ingen app på den här adressen." 404', "\t}", "}"]
    return "\n".join(lines) + "\n"


def apply(log=None) -> None:
    """Skriv Caddyfile och be Caddy läsa in den. Tyst om proxyn inte kör."""
    PROXY_DIR.mkdir(parents=True, exist_ok=True)
    text = render()
    CADDYFILE.write_text(text, "utf-8")
    os.chmod(CADDYFILE, 0o600)
    if not SOCKET.exists():
        if log:
            log("Proxyn (wizzos-proxy) kör inte – inställningen används när den startar.")
        return
    transport = httpx.HTTPTransport(uds=str(SOCKET))
    with httpx.Client(transport=transport, base_url="http://caddy", timeout=30) as c:
        r = c.post("/load", content=text.encode(),
                   headers={"Content-Type": "text/caddyfile"})
        if r.status_code >= 400:
            raise RuntimeError(f"Proxyn tog inte emot inställningen: {r.text[:300]}")
    if log:
        log("Proxyn har läst in de nya adresserna.")


def safe_apply() -> None:
    try:
        apply()
    except Exception:  # noqa: BLE001
        pass


def cert_ready(domain: str) -> bool:
    pattern = str(PROXY_DIR / "data" / "caddy" / "certificates" / "*" / f"wildcard_.{domain}" / "*.crt")
    return bool(glob.glob(pattern))


def enable(domain: str, ip: str, token: str | None, log) -> dict:
    domain = domain.strip().lower().rstrip(".")
    ip = ip.strip()
    if not DOMAIN_RE.match(domain):
        raise ValueError("Skriv en domän som t.ex. hem.exempel.se")
    if not re.match(r"^[0-9a-fA-F:.]+$", ip):
        raise ValueError("Skriv en IP-adress, t.ex. 100.64.0.1")
    s = config.load()
    token = (token or "").strip() or s.get("cf_token", "")
    if not token:
        raise ValueError("Klistra in Cloudflare-nyckeln.")
    log("Kontrollerar Cloudflare-nyckeln ...")
    verify_token(token)
    upsert_dns(token, domain, ip, log)
    config.update(cf_token=token, proxy_domain=domain, proxy_ip=ip, proxy_enabled=True,
                  proxy_status="Väntar på certifikat")
    apply(log)
    log("Hämtar certifikat från Let's Encrypt (brukar ta 30–90 sekunder) ...")
    for _ in range(60):
        if cert_ready(domain):
            config.update(proxy_status="Aktiv")
            log(f"Klart! WizzOS nås nu på https://{domain}")
            return {"url": f"https://{domain}"}
        time.sleep(3)
    config.update(proxy_status="Certifikatet dröjer")
    log("Certifikatet har inte kommit än. Det kan ta några minuter till – titta i proxyns logg om det inte löser sig.")
    return {"url": f"https://{domain}"}


def disable(log) -> None:
    config.update(proxy_enabled=False, proxy_status="")
    apply(log)
    log("Egna adresser är avstängda. DNS-posterna hos Cloudflare finns kvar och gör ingen skada.")


def proxy_logs() -> str:
    import subprocess
    r = subprocess.run(["docker", "logs", "--tail", "200", "wizzos-proxy"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=30)
    out = r.stdout + r.stderr
    # Visa aldrig nyckeln i loggar.
    tok = config.load().get("cf_token")
    return out.replace(tok, "•••") if tok else out
