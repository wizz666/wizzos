"""Installera och hantera appar med docker compose."""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import yaml

from . import config, jobs, proxy

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
KNOWN_VARS = ("AppID", "PUID", "PGID", "TZ")


# ---------- hjälpare ----------

def arch() -> str:
    m = platform.machine().lower()
    return {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64",
            "armv7l": "arm", "i686": "386"}.get(m, m)


def docker(*args: str, timeout: int = 60) -> str:
    r = subprocess.run(["docker", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip() or "docker-fel")
    return r.stdout


def docker_ok() -> tuple[bool, str]:
    try:
        return True, docker("version", "--format", "{{.Server.Version}}", timeout=10).strip()
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def substitute(text: str, values: dict) -> str:
    """Byt ut $AppID, ${PUID} osv. Andra variabler lämnas åt docker compose."""
    def rep(m):
        name = m.group(1) or m.group(2)
        return str(values[name]) if name in values else m.group(0)
    pattern = r"\$\{(" + "|".join(KNOWN_VARS) + r")\}|\$(" + "|".join(KNOWN_VARS) + r")\b"
    return re.sub(pattern, rep, text)


def _host_exists(path: str) -> bool | None:
    """Finns sökvägen på värden? None = kan inte avgöra."""
    if not config.HOST_ROOT.is_dir():
        return None
    return (config.HOST_ROOT / path.lstrip("/")).exists()


def _parse_short_port(p: str) -> dict:
    proto = "tcp"
    if "/" in p:
        p, proto = p.rsplit("/", 1)
    parts = p.split(":")
    host_ip = ""
    if len(parts) == 3:
        host_ip, published, target = parts
    elif len(parts) == 2:
        published, target = parts
    else:
        published, target = "", parts[0]
    d = {"target": int(target.split("-")[0]) if target.isdigit() else target,
         "published": published, "protocol": proto}
    if host_ip:
        d["host_ip"] = host_ip
    return d


def _norm_ports(service: dict) -> list[dict]:
    out = []
    for p in service.get("ports") or []:
        if isinstance(p, (str, int)):
            out.append(_parse_short_port(str(p)))
        elif isinstance(p, dict):
            out.append(dict(p))
    return out


def _port_descriptions(service: dict) -> dict:
    x = (service.get("x-casaos") or {}).get("ports") or []
    from .store import pick
    return {str(i.get("container")): pick(i.get("description")) for i in x if isinstance(i, dict)}


def _env_descriptions(service: dict) -> dict:
    x = (service.get("x-casaos") or {}).get("envs") or []
    from .store import pick
    return {str(i.get("container")): pick(i.get("description")) for i in x if isinstance(i, dict)}


def _env_dict(service: dict) -> dict:
    env = service.get("environment") or {}
    if isinstance(env, list):
        d = {}
        for item in env:
            k, _, v = str(item).partition("=")
            d[k] = v
        return d
    return {str(k): ("" if v is None else str(v)) for k, v in env.items()}


def _make_owned_dir(path: Path, created: list | None = None) -> None:
    """Skapa mappen (och saknade föräldrar under /DATA) ägd av PUID/PGID,
    så att appar som inte kör som root kan skriva i den. Nyskapade mappar
    läggs i `created` (används av Prova först för att städa spårlöst)."""
    s = config.load()
    missing = []
    p = path
    while not p.exists() and p != config.DATA_ROOT and p != p.parent:
        missing.append(p)
        p = p.parent
    path.mkdir(parents=True, exist_ok=True)
    if created is not None and missing:
        created.append(str(missing[-1]))  # översta nyskapade mappen räcker
    if not hasattr(os, "chown"):
        return
    for d in missing:
        try:
            os.chown(d, int(s["puid"]), int(s["pgid"]))
        except (OSError, ValueError):
            pass


def _values(name: str) -> dict:
    s = config.load()
    return {"AppID": name, "PUID": s["puid"], "PGID": s["pgid"], "TZ": s["tz"]}


# ---------- inför installation ----------

def used_ports() -> set[int]:
    used = set()
    try:
        out = docker("ps", "--format", "{{.Ports}}")
    except Exception:  # noqa: BLE001
        return used
    for m in re.finditer(r":(\d+)->", out):
        used.add(int(m.group(1)))
    return used


def install_form(compose_text: str, name: str) -> dict:
    """Det installationsrutan visar: portar och miljövariabler som går att ändra."""
    doc = yaml.safe_load(substitute(compose_text, _values(name)))
    used = used_ports()
    ports, envs = [], []
    for sname, svc in doc["services"].items():
        pdesc = _port_descriptions(svc)
        for p in _norm_ports(svc):
            pub = str(p.get("published") or "")
            ports.append({"service": sname, "target": str(p["target"]),
                          "published": pub, "protocol": p.get("protocol", "tcp"),
                          "description": pdesc.get(str(p["target"]), ""),
                          "busy": pub.isdigit() and int(pub) in used})
        edesc = _env_descriptions(svc)
        for k, v in _env_dict(svc).items():
            if k in ("PUID", "PGID", "TZ"):
                continue
            envs.append({"service": sname, "key": k, "value": v,
                         "description": edesc.get(k, "")})
    return {"name": name, "ports": ports, "envs": envs,
            "installed": (config.APPS_DIR / name).exists()}


def build_compose(compose_text: str, name: str, port_overrides: dict | None = None,
                  env_overrides: dict | None = None,
                  created: list | None = None) -> tuple[dict, list[str]]:
    """Gör butikens compose redo för just den här servern. Returnerar (compose, noteringar)."""
    port_overrides = port_overrides or {}
    env_overrides = env_overrides or {}
    notes: list[str] = []
    bind_ip = config.load().get("bind_ip", "").strip()
    doc = yaml.safe_load(substitute(compose_text, _values(name)))
    doc["name"] = name
    for sname, svc in doc["services"].items():
        # Portar
        raw_ports = svc.get("ports") or []
        if any(isinstance(p, str) and "-" in p for p in raw_ports):
            # Portintervall (t.ex. 6881-6889) lämnas som de är.
            if bind_ip:
                svc["ports"] = [p if p.count(":") >= 2 or ":" not in p else f"{bind_ip}:{p}"
                                for p in map(str, raw_ports)]
        else:
            ports = []
            for p in _norm_ports(svc):
                key = f"{sname}:{p['target']}/{p.get('protocol', 'tcp')}"
                if key in port_overrides and str(port_overrides[key]).strip():
                    p["published"] = str(port_overrides[key]).strip()
                if not str(p.get("published") or "").strip():
                    p.pop("published", None)
                if bind_ip:
                    p["host_ip"] = bind_ip
                ports.append(p)
            if ports:
                svc["ports"] = ports
        # Miljövariabler
        env = _env_dict(svc)
        if env or any(k.startswith(sname + ":") for k in env_overrides):
            for k, v in env_overrides.items():
                s, _, key = k.partition(":")
                if s == sname:
                    env[key] = v
            svc["environment"] = env
        # Enheter (t.ex. /dev/dri) som inte finns på den här datorn tas bort,
        # annars vägrar docker starta appen.
        devs = []
        for d in svc.get("devices") or []:
            src = (d if isinstance(d, str) else d.get("source", "")).split(":")[0]
            if src and _host_exists(src) is False:
                notes.append(f"Hoppar över enheten {src} (finns inte här).")
                continue
            devs.append(d)
        if "devices" in svc:
            if devs:
                svc["devices"] = devs
            else:
                del svc["devices"]
        # Mappar: skapa de som ska ligga under /DATA, hoppa över saknade
        # systemsökvägar (t.ex. Raspberry Pi-bibliotek).
        vols = []
        for v in svc.get("volumes") or []:
            if isinstance(v, str):
                src = v.split(":")[0]
                is_bind = src.startswith("/") or src.startswith(".")
            else:
                src = str(v.get("source", ""))
                is_bind = v.get("type") == "bind"
            if is_bind and src.startswith("/"):
                if src.startswith(str(config.DATA_ROOT) + "/") or src == str(config.DATA_ROOT):
                    looks_like_file = bool(re.search(r"\.[a-z0-9]{2,5}$", src, re.I))
                    target = Path(src).parent if looks_like_file else Path(src)
                    _make_owned_dir(target, created)
                elif _host_exists(src) is False and not src.startswith("/var/run/"):
                    notes.append(f"Hoppar över mappen {src} (finns inte här).")
                    continue
            vols.append(v)
        if "volumes" in svc:
            svc["volumes"] = vols
        labels = svc.get("labels") or {}
        if isinstance(labels, list):
            labels = dict(str(x).partition("=")[::2] for x in labels)
        labels["wizzos.app"] = name
        svc["labels"] = labels
    return doc, notes


def _web_port(meta_port: str, compose_text: str, doc: dict, main: str) -> str:
    """Butikens port_map är den publicerade porten. Om användaren ändrat den
    porten, följ med till det nya värdet."""
    if not meta_port:
        return ""
    try:
        orig = yaml.safe_load(compose_text)["services"].get(main) or {}
    except Exception:  # noqa: BLE001
        return meta_port
    new = doc["services"].get(main) or {}
    for a, b in zip(_norm_ports(orig), _norm_ports(new)):
        if str(a.get("published")) == meta_port:
            return str(b.get("published"))
    return meta_port


def _gui_https_port(doc: dict, main: str) -> str:
    """LinuxServer-appar med skrivbord i webbläsaren (KasmVNC/Selkies) har
    port 3000 (http) och 3001 (https). Den nya versionen kräver https, så
    länka till 3001. Returnerar den publicerade porten för 3001, annars ''."""
    svc = (doc.get("services") or {}).get(main) or {}
    ports = _norm_ports(svc)
    targets = {str(p.get("target")): str(p.get("published") or "") for p in ports}
    if "3000" in targets and targets.get("3001"):
        return targets["3001"]
    return ""


# ---------- installera / hantera ----------

def app_dir(name: str) -> Path:
    if not NAME_RE.match(name):
        raise ValueError("Ogiltigt appnamn")
    return config.APPS_DIR / name


def _local_images() -> set[str]:
    try:
        out = docker("images", "--format", "{{.Repository}}:{{.Tag}}")
    except Exception:  # noqa: BLE001
        return set()
    return {x.strip() for x in out.splitlines() if x.strip()}


def install(app: dict, name: str, port_overrides: dict, env_overrides: dict,
            trial_minutes: int = 0) -> str:
    name = name.strip().lower()
    d = app_dir(name)
    if d.exists():
        raise ValueError(f"Det finns redan en app som heter {name}.")
    if app.get("architectures") and arch() not in app["architectures"]:
        raise ValueError(f"Appen finns inte för den här processorn ({arch()}).")
    created: list[str] = []
    doc, notes = build_compose(app["compose"], name, port_overrides, env_overrides, created)
    meta = {
        "name": name, "title": app["title"], "icon": app.get("icon", ""),
        "tagline": app.get("tagline", ""), "category": app.get("category", ""),
        "store_key": app.get("key", ""), "version": app.get("version", ""),
        "scheme": app.get("scheme", "http"), "index": app.get("index", "/"),
        "port": _web_port(app.get("port_map", ""), app["compose"], doc, app.get("main", "")),
        "main": app.get("main", ""),
    }
    gui = _gui_https_port(doc, meta["main"])
    if gui:
        meta["port"], meta["scheme"] = gui, "https"
    if trial_minutes:
        now = int(time.time())
        meta["trial"] = {"started": now, "until": now + trial_minutes * 60,
                         "dirs": created, "images": []}
    images_before = _local_images() if trial_minutes else set()

    def work(log):
        for n in notes:
            log(n)
        d.mkdir(parents=True)
        (d / "docker-compose.yml").write_text(
            yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), "utf-8")
        (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
        vals = _values(name)
        (d / ".env").write_text("".join(f"{k}={v}\n" for k, v in vals.items()), "utf-8")
        try:
            from . import recipes
            recipes.prepare(app.get("key", ""), name, log)  # WizzOS-paket: färdiga inställningsfiler
            log("Hämtar programmet (kan ta några minuter första gången) ...")
            jobs.run_cmd(_compose(name, "pull"), log)
            if trial_minutes:
                # Kom ihåg vilka program som hämtades nu, så att de kan tas
                # bort igen när provtiden är slut.
                meta["trial"]["images"] = sorted(_local_images() - images_before)
                (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
            log("Startar ...")
            jobs.run_cmd(_compose(name, "up", "-d", "--remove-orphans"), log)
            proxy.safe_apply()
            url = next((a["url"] for a in installed()["apps"] if a["name"] == name), "")
            log(f"Klart! {meta['title']} är installerad." + (f" Adress: {url}" if url else ""))
            if trial_minutes:
                log(f"🧪 Provläge: {meta['title']} tas bort spårlöst om {trial_minutes} minuter "
                    "om du inte trycker på Behåll.")
            return {"app": name, "trial": bool(trial_minutes)}
        except Exception:
            log("Installationen misslyckades, städar upp ...")
            subprocess.run(_compose(name, "down", "--remove-orphans", *(["-v"] if trial_minutes else [])),
                           capture_output=True)
            shutil.rmtree(d, ignore_errors=True)
            if trial_minutes:
                from . import trial
                trial.remove_dirs(created, log)
            raise

    return jobs.start(f"Installerar {meta['title']}", work)


def install_custom(name: str, title: str, compose_text: str, port: str, icon: str) -> str:
    try:
        parsed = yaml.safe_load(compose_text)
        assert isinstance(parsed, dict) and isinstance(parsed.get("services"), dict)
    except Exception as e:  # noqa: BLE001
        raise ValueError("Det där ser inte ut som en giltig docker-compose-fil.") from e
    x = parsed.get("x-casaos") or {}
    app = {"compose": compose_text, "title": title or name, "icon": icon,
           "port_map": port or str(x.get("port_map") or ""), "scheme": "http", "index": "/",
           "main": x.get("main") or next(iter(parsed["services"])), "key": "custom"}
    return install(app, name, {}, {})


def _compose(name: str, *args: str) -> list[str]:
    d = app_dir(name)
    return ["docker", "compose", "-p", name, "--project-directory", str(d),
            "-f", str(d / "docker-compose.yml"), *args]


def _containers() -> list[dict]:
    try:
        out = docker("ps", "-a", "--no-trunc", "--format", "{{json .}}")
    except Exception:  # noqa: BLE001
        return []
    rows = []
    for line in out.splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


def _project(row: dict) -> str:
    m = re.search(r"com\.docker\.compose\.project=([^,]+)", row.get("Labels", ""))
    return m.group(1) if m else ""


def installed() -> dict:
    """Mina appar + andra containrar som redan kör på datorn."""
    rows = _containers()
    by_project: dict[str, list[dict]] = {}
    for r in rows:
        by_project.setdefault(_project(r), []).append(r)
    apps = []
    if config.APPS_DIR.exists():
        for d in sorted(config.APPS_DIR.iterdir()):
            mf = d / "meta.json"
            if not mf.exists():
                continue
            meta = json.loads(mf.read_text("utf-8"))
            if meta.get("scheme") != "https":
                try:
                    gui = _gui_https_port(
                        yaml.safe_load((d / "docker-compose.yml").read_text("utf-8")),
                        meta.get("main", ""))
                except Exception:  # noqa: BLE001
                    gui = ""
                if gui:
                    meta["port"], meta["scheme"] = gui, "https"
                    mf.write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
            cs = by_project.pop(meta["name"], [])
            states = [c.get("State", "") for c in cs]
            if cs and all(s == "running" for s in states):
                status = "running"
            elif any(s == "running" for s in states):
                status = "partial"
            elif any(s == "restarting" for s in states):
                status = "restarting"
            else:
                status = "stopped"
            meta["status"] = status
            meta["containers"] = len(cs)
            apps.append(meta)
    proxy.assign_addresses(apps)
    others = []
    for proj, cs in by_project.items():
        if proj.startswith("wzt-"):  # återställningstestets sandlåda
            continue
        for c in cs:
            if "wizzos/wizzos" in c.get("Image", "") or c.get("Names") == "wizzos":
                continue
            ports = sorted({int(p) for p in re.findall(r":(\d+)->", c.get("Ports", ""))})
            others.append({"id": c["ID"][:12], "name": c.get("Names", ""),
                           "image": c.get("Image", ""), "state": c.get("State", ""),
                           "status": c.get("Status", ""), "project": proj, "ports": ports})
    others.sort(key=lambda c: c["name"])
    return {"apps": apps, "others": others}


# ---------- hälsokoll och skyddade ändringar ----------

_busy: set[str] = set()
_busy_lock = threading.Lock()


def guarded_job(name: str, title: str, work) -> str:
    """Kör ett jobb för en app, men aldrig två samtidigt för samma app."""
    with _busy_lock:
        if name in _busy:
            raise ValueError("Appen håller redan på med något. Vänta en stund.")
        _busy.add(name)

    def run(log):
        try:
            return work(log)
        finally:
            with _busy_lock:
                _busy.discard(name)
    return jobs.start(title, run)


def _project_state(name: str) -> list[dict]:
    try:
        ids = docker("ps", "-a", "-q", "--filter",
                     f"label=com.docker.compose.project={name}").split()
        if not ids:
            return []
        raw = docker("inspect", *ids)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for c in json.loads(raw):
        st = c.get("State", {})
        out.append({"name": c.get("Name", "").lstrip("/"), "status": st.get("Status"),
                    "exit": st.get("ExitCode", 0), "restarts": c.get("RestartCount", 0),
                    "health": (st.get("Health") or {}).get("Status", "")})
    return out


def wait_healthy(name: str, log, timeout: int = 150, stable: int = 30) -> tuple[bool, str]:
    """Vänta tills appen har gått stabilt i `stable` sekunder.
    Misslyckas om den kraschar, startar om gång på gång eller blir 'unhealthy'."""
    log(f"Kontrollerar att appen mår bra (ca {stable} sekunder) ...")
    start = time.time()
    first = {c["name"]: c["restarts"] for c in _project_state(name)}
    ok_since = None
    while time.time() - start < timeout:
        cs = _project_state(name)
        if not cs:
            return False, "Appen har inga containrar."
        for c in cs:
            if c["health"] == "unhealthy":
                return False, f"{c['name']} rapporterar att den inte mår bra."
            if c["status"] == "exited" and c["exit"] != 0:
                return False, f"{c['name']} stannade med felkod {c['exit']}."
            if c["restarts"] - first.get(c["name"], 0) >= 2:
                return False, f"{c['name']} startar om gång på gång."
        good = all(c["status"] == "running" and c["health"] in ("", "healthy")
                   or (c["status"] == "exited" and c["exit"] == 0) for c in cs)
        if good:
            ok_since = ok_since or time.time()
            if time.time() - ok_since >= stable:
                return True, ""
        else:
            ok_since = None
        time.sleep(3)
    return False, "Appen kom aldrig igång ordentligt."


def _rollback(name: str, sid: str, reason: str, log, notify: bool = True) -> bool:
    from . import snapshots
    log("")
    log(f"⚠ {reason}")
    log("Återställer automatiskt till hur det var innan ...")
    snapshots.restore(name, sid, log, safety=False)
    ok, why = wait_healthy(name, log, stable=15)
    log("Appen är tillbaka som förut." if ok else f"Obs: appen mår inte bra ens efter återställningen ({why}).")
    if not notify:
        return ok
    from . import alerts
    alerts.notify(f"Ändring av {name} backades automatiskt",
                  f"{reason} WizzOS återställde appen till hur den var innan"
                  + (" – den fungerar igen." if ok else f", men den mår fortfarande inte bra ({why})."),
                  3 if ok else 5)
    return ok


def image_version(ref: str) -> str:
    """Versionsetiketten i en avbildning (om utvecklaren satt en)."""
    try:
        v = docker("image", "inspect", "-f",
                   '{{index .Config.Labels "org.opencontainers.image.version"}}', ref).strip()
    except Exception:  # noqa: BLE001
        return ""
    return "" if v in ("", "<no value>") else v[:40]


def do_update(name: str, log, notify: bool = True) -> dict:
    """Själva säkra uppdateringen. Returnerar
    {"changed": bool, "ok": bool, "rolled_back": bool, "why": str, "snapshot": id,
     "from": version, "to": version}. Kastar inte vid trasig ny version."""
    from . import snapshots
    meta = json.loads((app_dir(name) / "meta.json").read_text("utf-8"))
    before = snapshots.service_images(name)
    old_ver = next((image_version(i["id"]) for i in before.values() if image_version(i["id"])), "")
    log("Letar efter ny version ...")
    jobs.run_cmd(_compose(name, "pull"), log)
    after = {}
    for svc, img in before.items():
        try:
            after[svc] = docker("image", "inspect", "-f", "{{.Id}}", img["ref"]).strip()
        except Exception:  # noqa: BLE001
            after[svc] = img["id"]
    if before and all(after.get(s) == i["id"] for s, i in before.items()):
        log("Du har redan den senaste versionen. Inget behövde ändras.")
        return {"changed": False, "ok": True, "rolled_back": False}
    new_ver = next((image_version(after[s]) for s in after if after[s] != before[s]["id"] and image_version(after[s])), "")
    log("Ny version hittad" + (f": {old_ver or '?'} → {new_ver}" if new_ver else "") + ".")
    # Den gamla versionen finns kvar i Docker under sitt id; punkten taggar den.
    snap = snapshots.create(name, "update", log, start_after=False, images=before)
    res = {"changed": True, "app": name, "snapshot": snap["id"], "from": old_ver, "to": new_ver}
    log("Startar den nya versionen ...")
    try:
        jobs.run_cmd(_compose(name, "up", "-d", "--remove-orphans"), log)
    except Exception as e:  # noqa: BLE001
        why = f"Den nya versionen gick inte att starta ({e})."
        back = _rollback(name, snap["id"], why, log, notify)
        return {**res, "ok": False, "rolled_back": True, "back_ok": back, "why": why}
    ok, why = wait_healthy(name, log)
    if not ok:
        why = f"Den nya versionen verkar trasig: {why}"
        back = _rollback(name, snap["id"], why, log, notify)
        return {**res, "ok": False, "rolled_back": True, "back_ok": back, "why": why}
    log(f"Klart! {meta.get('title', name)} är uppdaterad och mår bra.")
    return {**res, "ok": True, "rolled_back": False}


def update(name: str) -> str:
    """Säker uppdatering: spara version → hämta ny → hälsokoll → ångra vid fel."""
    from . import snapshots
    if not app_dir(name).exists():
        raise ValueError("Appen finns inte.")
    meta = json.loads((app_dir(name) / "meta.json").read_text("utf-8"))

    def work(log):
        r = do_update(name, log)
        if not r["ok"]:
            raise RuntimeError("Uppdateringen misslyckades – appen är återställd.")
        return r

    return guarded_job(name, f"Uppdaterar {meta.get('title', name)}", work)


def action(name: str, what: str) -> str | None:
    """start / stop / restart direkt; update som skyddat jobb."""
    d = app_dir(name)
    if not d.exists():
        raise ValueError("Appen finns inte.")
    if what in ("start", "stop", "restart"):
        r = subprocess.run(_compose(name, what), capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or "Misslyckades")
        # Kom ihåg om du själv stoppat appen – då larmar WizzOS inte för den.
        mf = d / "meta.json"
        meta = json.loads(mf.read_text("utf-8"))
        meta["user_stopped"] = what == "stop"
        mf.write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
        return None
    if what == "update":
        return update(name)
    raise ValueError("Okänd åtgärd")


def uninstall(name: str, delete_data: bool) -> str:
    from . import snapshots
    d = app_dir(name)
    if not d.exists():
        raise ValueError("Appen finns inte.")
    title = json.loads((d / "meta.json").read_text("utf-8")).get("title", name)

    def work(log):
        try:
            snapshots.create(name, "uninstall", log, start_after=False)
        except Exception as e:  # noqa: BLE001
            log(f"Kunde inte spara en återställningspunkt ({e}). Datan lämnas kvar för säkerhets skull.")
            delete_data_now = False
        else:
            delete_data_now = delete_data
        jobs.run_cmd(_compose(name, "down", "--remove-orphans"), log)
        if delete_data_now:
            data = config.DATA_ROOT / "AppData" / name
            if data.exists():
                log(f"Raderar data i {data}")
                shutil.rmtree(data, ignore_errors=True)
        shutil.rmtree(d, ignore_errors=True)
        proxy.safe_apply()
        log(f"Avinstallerad. Ångrar du dig finns {title} under \"Nyligen borttagna\" i 30 dagar.")
        return {"app": name}
    return guarded_job(name, f"Tar bort {title}", work)


RESERVED_SUBS = {"www", "wizz", "wizzos", "admin", "api", "mail", "localhost"}


def set_address(name: str, sub: str) -> dict:
    """Byt appens adressnamn (det som står före domänen). Tomt = automatiskt."""
    mf = app_dir(name) / "meta.json"
    if not mf.exists():
        raise ValueError("Appen finns inte.")
    sub = sub.strip().lower().rstrip(".")
    if sub:
        if not proxy.SUB_RE.match(sub):
            raise ValueError("Använd bara a–z, 0–9 och bindestreck (inte först eller sist), högst 63 tecken.")
        if sub in RESERVED_SUBS:
            raise ValueError(f"\"{sub}\" är reserverat. Välj ett annat namn.")
        for other in installed()["apps"]:
            if other["name"] != name and other.get("address") == sub:
                raise ValueError(f"{other['title']} använder redan den adressen.")
    meta = json.loads(mf.read_text("utf-8"))
    if sub:
        meta["sub"] = sub
    else:
        meta.pop("sub", None)
    mf.write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
    proxy.apply()
    me = next(a for a in installed()["apps"] if a["name"] == name)
    return {"address": me["address"], "url": me["url"]}


def snapshot_now(name: str) -> str:
    from . import snapshots
    if not app_dir(name).exists():
        raise ValueError("Appen finns inte.")

    def work(log):
        info = snapshots.create(name, "manual", log)
        return {"app": name, "snapshot": info["id"]}
    return guarded_job(name, "Sparar återställningspunkt", work)


def restore_snapshot(name: str, sid: str) -> str:
    from . import snapshots

    def work(log):
        reinstall = not app_dir(name).exists()
        snapshots.restore(name, sid, log, safety=True)
        proxy.safe_apply()
        ok, why = wait_healthy(name, log, stable=15)
        if ok:
            log("Klart! Appen är återställd." if not reinstall else "Klart! Appen är tillbaka.")
        else:
            log(f"Appen är återställd men verkar inte må bra: {why}")
        return {"app": name}
    return guarded_job(name, "Återställer", work)


def logs(name: str) -> str:
    r = subprocess.run(_compose(name, "logs", "--tail", "300", "--no-color", "--timestamps"),
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=30)
    return (r.stdout + r.stderr)[-60000:]


def read_compose(name: str) -> str:
    return (app_dir(name) / "docker-compose.yml").read_text("utf-8")


def save_compose(name: str, text: str) -> str:
    try:
        parsed = yaml.safe_load(text)
        assert isinstance(parsed, dict) and isinstance(parsed.get("services"), dict)
    except Exception as e:  # noqa: BLE001
        raise ValueError("Filen är inte giltig YAML med services.") from e
    from . import snapshots
    f = app_dir(name) / "docker-compose.yml"

    def work(log):
        snap = snapshots.create(name, "settings", log)
        f.write_text(text, "utf-8")
        try:
            jobs.run_cmd(_compose(name, "up", "-d", "--remove-orphans"), log)
        except Exception as e:  # noqa: BLE001
            _rollback(name, snap["id"], "Appen gick inte att starta med ändringen.", log)
            raise RuntimeError("Ändringen fungerade inte – appen är återställd.") from e
        ok, why = wait_healthy(name, log)
        if not ok:
            _rollback(name, snap["id"], f"Appen mår inte bra med ändringen: {why}", log)
            raise RuntimeError("Ändringen fungerade inte – appen är återställd.")
        log("Ändringen är sparad och appen mår bra.")
        return {"app": name, "snapshot": snap["id"]}
    return guarded_job(name, "Sparar inställningar", work)


def container_action(cid: str, what: str) -> None:
    if what not in ("start", "stop", "restart"):
        raise ValueError("Okänd åtgärd")
    if not re.match(r"^[a-f0-9]{6,64}$", cid):
        raise ValueError("Ogiltigt id")
    docker(what, cid, timeout=120)


def container_logs(cid: str) -> str:
    if not re.match(r"^[a-f0-9]{6,64}$", cid):
        raise ValueError("Ogiltigt id")
    r = subprocess.run(["docker", "logs", "--tail", "300", "--timestamps", cid],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=30)
    return (r.stdout + r.stderr)[-60000:]
