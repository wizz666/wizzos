"""Flytta in från Unraid: gör om Unraids appmallar (my-*.xml) till WizzOS-appar.

Mallarna ligger på Unraid-servern i /boot/config/plugins/dockerMan/templates-user/.
Varje mall blir en docker-compose-fil där Unraids mappar byts mot WizzOS egna:
  /mnt/user/appdata/<app>/...   →  /DATA/AppData/<nytt namn>/...
  /mnt/user/<utdelning>/...     →  /DATA/<utdelning>/...
Det som inte går att göra om automatiskt listas som "att kolla", inget gissas tyst.
Fält som Unraid märkt som hemliga (Mask) visas aldrig i förhandsvisningen.
"""
from __future__ import annotations

import io
import re
import shlex
import xml.etree.ElementTree as ET
import zipfile

import yaml

_APPDATA = re.compile(r"^/mnt/(?:user|cache|disk\d+|[\w-]+)/appdata/[^/]+/?(.*)$")
_SHARE = re.compile(r"^/mnt/(?:user|user0|disk\d+|cache|[\w-]+)/([^/]+)/?(.*)$")
_SKIP_VARS = re.compile(r"^(CA_TS_|TS_)")
MAX_FILES = 100


def clean_name(s: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", (s or "").lower()).strip("-")
    s = re.sub(r"^(binhex|linuxserver|my)-", "", s)
    return s[:40] or "app"


def _esc(v: str) -> str:
    """Docker compose läser $ som variabel – ett $ i t.ex. ett lösenord måste dubblas."""
    return str(v).replace("$", "$$")


def map_path(host: str, name: str) -> tuple[str, str]:
    """(ny sökväg, sort) där sort = appdata | share | system | other."""
    host = (host or "").rstrip("/") or "/"
    if not host.startswith("/"):
        return (host, "volume")  # namngiven Docker-volym – följer med som den är
    m = _APPDATA.match(host)
    if m:
        rest = m.group(1)
        return (f"/DATA/AppData/{name}/{rest}".rstrip("/"), "appdata")
    m = _SHARE.match(host)
    if m and m.group(1) not in ("user", "remotes", "disks", "addons", "rootshare"):
        rest = m.group(2)
        return (f"/DATA/{m.group(1)}/{rest}".rstrip("/"), "share")
    if host.startswith(("/var/run/docker.sock", "/dev/", "/sys/", "/proc/", "/etc/localtime")):
        return (host, "system")
    return (host, "other")


def _extra(params: str, svc: dict, warn: list) -> None:
    try:
        toks = shlex.split(params or "")
    except ValueError:
        warn.append(f"Kunde inte läsa extra-inställningarna: {params}")
        return
    i = 0
    while i < len(toks):
        t = toks[i]
        if "=" in t and t.startswith("--"):
            flag, val = t.split("=", 1)
        else:
            flag, val = t, (toks[i + 1] if i + 1 < len(toks) and not toks[i + 1].startswith("-") else None)
            if val is not None:
                i += 1
        i += 1
        if flag == "--user":
            svc["user"] = "${PUID}:${PGID}" if val in ("99:100", "nobody:users") else val
        elif flag in ("--memory", "-m"):
            svc["mem_limit"] = val.lower()
        elif flag == "--restart":
            svc["restart"] = val
        elif flag == "--cap-add":
            svc.setdefault("cap_add", []).append(val)
        elif flag == "--hostname":
            svc["hostname"] = val
        elif flag == "--dns":
            svc.setdefault("dns", []).append(val)
        elif flag == "--sysctl":
            k, _, v = (val or "").partition("=")
            svc.setdefault("sysctls", {})[k] = v
        elif flag == "--device":
            svc.setdefault("devices", []).append(val)
        elif flag == "--shm-size":
            svc["shm_size"] = val
        elif flag in ("-e", "--env"):
            k, _, v = (val or "").partition("=")
            svc.setdefault("environment", {})[k] = _esc(v)
        elif flag == "--privileged":
            svc["privileged"] = True
        elif flag == "--log-opt":
            pass  # WizzOS sköter loggstorleken själv
        elif flag == "--gpus":
            warn.append("Appen använde grafikkortet (--gpus). Det kräver drivrutiner på den nya servern – inte inlagt.")
        else:
            warn.append(f"Inställningen {flag} {val or ''} gjordes inte om – kolla om den behövs.".replace("  ", " "))


def convert(xml_text: str) -> dict:
    """En Unraid-mall → förslag på WizzOS-app."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise ValueError(f"Det där är ingen Unraid-mall ({e}).") from e
    if root.tag != "Container":
        raise ValueError("Det där är ingen Unraid-mall.")
    g = lambda tag: (root.findtext(tag) or "").strip()  # noqa: E731
    title = g("Name") or "App"
    name = clean_name(title)
    image = g("Repository")
    if not image:
        raise ValueError(f"{title}: mallen saknar image.")
    warn: list[str] = []
    svc: dict = {"image": image, "container_name": name, "restart": "unless-stopped"}
    folders: list[dict] = []
    named: set[str] = set()
    secrets: list[str] = []
    ports: list[str] = []
    port_by_target: dict[str, str] = {}
    net = g("Network").lower()
    if net == "host":
        svc["network_mode"] = "host"
    elif net not in ("", "bridge"):
        warn.append(f"Appen låg i nätverket ”{g('Network')}” med egen IP-adress. Här får den vanliga portar i stället.")
    if g("Privileged").lower() == "true":
        svc["privileged"] = True
        warn.append("Appen kör med full tillgång till servern (privileged) – precis som på Unraid.")
    for c in root.findall("Config"):
        typ, target = c.get("Type", ""), (c.get("Target") or "").strip()
        value = (c.text or "").strip()
        masked = c.get("Mask") == "true"
        if typ == "Port" and target and value:
            proto = c.get("Mode") or "tcp"
            port_by_target[target] = value
            if svc.get("network_mode") != "host":
                ports.append(f"{value}:{target}" + ("/udp" if proto == "udp" else ""))
        elif typ == "Path" and target and value:
            new, kind = map_path(value, name)
            mode = c.get("Mode") or "rw"
            svc.setdefault("volumes", []).append(f"{new}:{target}" + (":ro" if mode.startswith("ro") else ""))
            if kind in ("appdata", "share"):
                folders.append({"from": value, "to": new, "kind": kind, "label": c.get("Name") or target})
            elif kind == "volume":
                named.add(new)
                warn.append(f"Docker-volymen ”{new}” skapas tom på nya servern – innehållet följer inte med automatiskt.")
            elif kind == "other":
                warn.append(f"Mappen {value} finns kanske inte på den nya servern – kolla den.")
            if value.startswith("/var/run/docker.sock"):
                warn.append("Appen styr Docker (docker.sock) – den kan påverka alla appar.")
        elif typ == "Variable" and target and not _SKIP_VARS.match(target):
            if value == "" and not c.get("Required") == "true":
                continue
            if target in ("PUID",) and value == "99":
                value = "${PUID}"
            elif target in ("PGID",) and value == "100":
                value = "${PGID}"
            elif target == "TZ":
                value = "${TZ}"
            else:
                value = _esc(value)
            svc.setdefault("environment", {})[target] = value
            if masked or (re.search(r"pass|secret|token|key", target, re.I) and not target.upper().endswith(("_PATH", "_DIR", "_FILE"))):
                secrets.append(target)
        elif typ == "Device" and value:
            svc.setdefault("devices", []).append(value)
        elif typ == "Label" and target:
            svc.setdefault("labels", {})[target] = _esc(value)
    if ports:
        svc["ports"] = ports
    _extra(g("ExtraParams"), svc, warn)
    if g("PostArgs"):
        svc["command"] = g("PostArgs")
    web = ""
    m = re.search(r"\[PORT:(\d+)\]", g("WebUI"))
    if m:
        web = port_by_target.get(m.group(1), m.group(1))
    scheme = "https" if g("WebUI").lower().startswith("https") else "http"
    doc = {"services": {name: svc}}
    if named:
        doc["volumes"] = {v: {} for v in sorted(named)}
    compose = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
    icon = g("Icon")
    return {"name": name, "title": title, "image": image, "icon": icon if icon.startswith("https://") else "",
            "port": web, "scheme": scheme, "compose": compose, "folders": folders,
            "warnings": warn, "secrets": sorted(set(secrets)), "network": net or "bridge"}


def preview(item: dict) -> dict:
    """Samma förslag men med hemliga värden dolda – det här visas i webbläsaren."""
    doc = yaml.safe_load(item["compose"])
    svc = next(iter(doc["services"].values()))
    env = svc.get("environment") or {}
    hidden = {str(env[k]) for k in item["secrets"] if env.get(k)}
    for k, v in list(env.items()):
        if str(v) in hidden:
            env[k] = "••••••"  # även fält med samma värde som ett hemligt
    shown = dict(item)
    shown["compose"] = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
    return shown


def read_upload(filename: str, data: bytes) -> list[tuple[str, str]]:
    """En .xml eller en .zip med mallar → [(filnamn, text)]."""
    if filename.lower().endswith(".zip"):
        out = []
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist():
                base = info.filename.rsplit("/", 1)[-1]
                if base.lower().endswith(".xml") and info.file_size < 1_000_000:
                    out.append((base, z.read(info).decode("utf-8", "replace")))
                if len(out) >= MAX_FILES:
                    break
        return out
    return [(filename, data.decode("utf-8", "replace"))]


def scan(files: list[tuple[str, str]], installed: set[str]) -> list[dict]:
    out = []
    for fname, text in files:
        try:
            item = convert(text)
            item["file"] = fname
            item["exists"] = item["name"] in installed
            out.append(item)
        except ValueError as e:
            out.append({"file": fname, "error": str(e)})
    return out
