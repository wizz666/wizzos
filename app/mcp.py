"""AI-koppling (MCP): låt Claude, ChatGPT m.fl. sköta WizzOS – med samma skyddsnät som du.

Protokoll: Model Context Protocol över HTTP (JSON-RPC på POST /mcp, "streamable
HTTP" utan strömning). Varje AI får en egen nyckel med en nivå:

  read  – Bara titta: status, appar, loggar, backup, säkerhet, kostnad, filer (lista)
  act   – Vanliga saker: dessutom installera på prov, starta/stoppa/starta om,
          uppdatera (med automatisk återställning), återställningspunkt, backup,
          delningslänkar

  all   – Allt, men du godkänner: dessutom ta bort, återställa, installera
          permanent m.m. Varje sådan sak blir en fråga i mobilen (approvals.py)
          och utförs först när du tryckt Godkänn.

Inställningar som rör säkerhet, backup och lösenord kan ingen AI ändra. Hemligheter
(lösenord, nycklar, tokens) lämnas aldrig ut, och loggar tvättas från sådant.
Allt AI:n gör skrivs i AI-dagboken.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time

from . import __version__, apps, config, jobs

STATE = config.HOME / "ai-keys.json"
DIARY = config.HOME / "ai-diary.json"
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
LEVELS = {"read": "Bara titta", "act": "Vanliga saker", "all": "Allt – du godkänner"}
RANK = {"read": 0, "act": 1, "all": 2}
_lock = threading.Lock()


# ---------- nycklar ----------

def _load() -> dict:
    try:
        return json.loads(STATE.read_text("utf-8"))
    except (OSError, ValueError):
        return {"keys": {}}


def _save(s: dict) -> None:
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1), "utf-8")
    import os
    os.chmod(tmp, 0o600)
    tmp.replace(STATE)


def enabled() -> bool:
    return bool(config.load().get("ai_link_enabled", True))


def set_enabled(on: bool) -> None:
    if bool(on) != enabled():
        config.update(ai_link_enabled=bool(on))
        diary("Du", "—", {}, True, "AI-kopplingen slogs " + ("på." if on else "av."))


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_key(name: str, level: str, days: int) -> dict:
    name = (name or "").strip()[:40] or "AI"
    if level not in LEVELS:
        raise ValueError("Välj nivå.")
    days = int(days)
    if days not in (0, 30, 90, 365):
        raise ValueError("Ogiltig giltighetstid.")
    kid = secrets.token_hex(4)
    token = "wzai_" + secrets.token_urlsafe(32)
    rec = {"name": name, "level": level, "created": int(time.time()),
           "expires": int(time.time()) + days * 86400 if days else 0, "last_used": 0, "uses": 0,
           "hash": _hash(token)}
    with _lock:
        s = _load()
        s["keys"][kid] = rec
        _save(s)
    diary(name, "—", {}, True, f"Ny nyckel skapad ({LEVELS[level]}).")
    return {"id": kid, "token": token, **public_key(kid, rec)}


def public_key(kid: str, r: dict) -> dict:
    return {"id": kid, "name": r["name"], "level": r["level"], "level_text": LEVELS[r["level"]],
            "created": r["created"], "expires": r["expires"], "last_used": r["last_used"], "uses": r["uses"],
            "web": bool(r.get("oauth"))}


def create_oauth_grant(name: str, level: str, days: int, client_id: str) -> tuple[str, str, str]:
    """Koppling via webben (OAuth): korta åtkomsttoken + roterande förnyelsetoken."""
    if level not in LEVELS:
        level = "read"
    kid = secrets.token_hex(4)
    access, refresh = "wzat_" + secrets.token_urlsafe(32), "wzrt_" + secrets.token_urlsafe(32)
    from .oauth import ACCESS_TTL
    rec = {"name": name[:60], "level": level, "created": int(time.time()),
           "expires": int(time.time()) + int(days) * 86400 if days else 0, "last_used": 0, "uses": 0, "hash": "",
           "oauth": {"client_id": client_id, "access": _hash(access), "access_exp": int(time.time()) + ACCESS_TTL,
                     "refresh": _hash(refresh)}}
    with _lock:
        s = _load()
        s["keys"][kid] = rec
        _save(s)
    diary(rec["name"], "—", {}, True, f"Kopplad via webben ({LEVELS[level]}).")
    return access, refresh, kid


def refresh_oauth_grant(refresh: str, client_id: str) -> tuple[str, str] | tuple[None, None]:
    from .oauth import ACCESS_TTL
    h = _hash(refresh)
    with _lock:
        s = _load()
        for kid, r in s["keys"].items():
            o = r.get("oauth") or {}
            if o.get("refresh") and hmac.compare_digest(o["refresh"], h) and o.get("client_id") == client_id:
                if r["expires"] and r["expires"] < time.time():
                    return None, None
                access, new_refresh = "wzat_" + secrets.token_urlsafe(32), "wzrt_" + secrets.token_urlsafe(32)
                o.update(access=_hash(access), access_exp=int(time.time()) + ACCESS_TTL, refresh=_hash(new_refresh))
                _save(s)
                return access, new_refresh
    return None, None


def keys() -> list[dict]:
    return sorted([public_key(k, r) for k, r in _load()["keys"].items()], key=lambda x: -x["created"])


def revoke(kid: str) -> None:
    with _lock:
        s = _load()
        r = s["keys"].pop(kid, None)
        if not r:
            raise ValueError("Nyckeln finns inte.")
        _save(s)
    diary(r["name"], "—", {}, True, "Nyckeln stängdes av.")


def check(auth_header: str) -> tuple[str, dict] | None:
    if not auth_header or not auth_header.lower().startswith("bearer "):
        return None
    h = _hash(auth_header[7:].strip())
    s = _load()
    for kid, r in s["keys"].items():
        o = r.get("oauth") or {}
        if (r.get("hash") and hmac.compare_digest(r["hash"], h)) or \
                (o.get("access") and hmac.compare_digest(o["access"], h) and o.get("access_exp", 0) > time.time()):
            if r["expires"] and r["expires"] < time.time():
                return None
            if time.time() - r["last_used"] > 60:
                with _lock:
                    s2 = _load()
                    if kid in s2["keys"]:
                        s2["keys"][kid]["last_used"] = int(time.time())
                        _save(s2)
            return kid, r
    return None


def _count(kid: str) -> None:
    with _lock:
        s = _load()
        if kid in s["keys"]:
            s["keys"][kid]["uses"] += 1
            s["keys"][kid]["last_used"] = int(time.time())
            _save(s)


# ---------- AI-dagbok ----------

def diary(who: str, tool: str, args: dict, ok: bool, text: str) -> None:
    with _lock:
        try:
            d = json.loads(DIARY.read_text("utf-8"))
        except (OSError, ValueError):
            d = []
        d.append({"time": int(time.time()), "who": who, "tool": tool,
                  "args": {k: str(v)[:80] for k, v in (args or {}).items()}, "ok": ok, "text": text[:300]})
        d = d[-500:]
        tmp = DIARY.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), "utf-8")
        tmp.replace(DIARY)


def read_diary(limit: int = 100) -> list[dict]:
    try:
        return json.loads(DIARY.read_text("utf-8"))[-limit:][::-1]
    except (OSError, ValueError):
        return []


# ---------- hjälp ----------

SECRET_RE = re.compile(r"(?i)((?:pass(?:word)?|pwd|secret|token|api[_-]?key|auth|bearer|private[_-]?key)\s*[=:]\s*|bearer\s+)(\S+)")


def scrub(text: str) -> str:
    return SECRET_RE.sub(lambda m: m.group(1) + "[dolt]", text)


def _app(name: str) -> dict:
    name = (name or "").strip().lower()
    for a in apps.installed()["apps"]:
        if a["name"] == name or a.get("title", "").lower() == name:
            return a
    raise ValueError(f"Det finns ingen app som heter {name}. Använd list_apps för att se namnen.")


def _wait(job_id: str, seconds: int = 25) -> dict:
    end = time.time() + seconds
    while time.time() < end:
        j = jobs.get(job_id)
        if not j or j["state"] != "running":
            break
        time.sleep(1)
    j = jobs.get(job_id) or {}
    return {"job": job_id, "state": {"running": "pågår fortfarande", "done": "klart", "failed": "misslyckades"}.get(j.get("state"), "okänt"),
            "error": j.get("error", ""), "log_tail": [scrub(x) for x in j.get("log", [])[-12:]], "result": j.get("result")}


def _url(a: dict) -> str:
    return a.get("url") or ""


# ---------- verktyg ----------

def t_status(_):
    from . import alerts, backup, system
    s = system.stats()
    ok, ver = apps.docker_ok()
    inst = apps.installed()["apps"]
    out = {"server": s["hostname"], "os": s["os"], "wizzos": __version__, "docker": ver if ok else f"svarar inte: {ver}",
           "cpu_procent": s["cpu"], "minne_procent": s["mem"]["percent"], "disk_procent": s["disk"]["percent"],
           "disk_ledigt_gb": round((s["disk"]["total"] - s["disk"]["used"]) / 1e9, 1), "drifttid_timmar": round(s["uptime"] / 3600, 1),
           "appar": len(inst), "appar_igang": sum(1 for a in inst if a["status"] == "running"),
           "aktiva_problem": [f"{p['title']}: {p['message']}" for p in alerts.public_cfg()["active"]],
           "backup": backup.summary(), "jobb_som_kor": jobs.running()}
    try:
        from . import security
        sec = security.cached()
        out["sakerhet"] = {"poang": sec["score"], "betyg": sec["grade"]}
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import cost
        c = cost.summary()
        out["kostnad_kr_per_manad"] = round(c["month_kr"], 1)
    except Exception:  # noqa: BLE001
        pass
    return out


def t_list_apps(_):
    from . import updater
    avail = updater.state().get("available") or {}
    res = []
    for a in apps.installed()["apps"]:
        res.append({"namn": a["name"], "titel": a.get("title"), "status": a["status"], "adress": _url(a),
                    "pa_prov_till": a["trial"]["until"] if a.get("trial") else None,
                    "ny_version_finns": a["name"] in avail, "stoppad_av_dig": bool(a.get("user_stopped")),
                    "nattliga_uppdateringar": not a.get("no_auto_update")})
    return {"appar": res}


def t_app_logs(a):
    app = _app(a.get("app"))
    lines = max(20, min(int(a.get("lines") or 120), 400))
    txt = scrub(apps.logs(app["name"]))
    return {"app": app["name"], "logg": "\n".join(txt.splitlines()[-lines:])}


def t_search_store(a):
    from . import store
    q = (a.get("query") or "").strip().lower()
    if not q:
        raise ValueError("Ange vad du söker efter.")
    installed = {x.get("store_key") for x in apps.installed()["apps"]}
    hits = []
    for x in store.all_apps(config.load()["stores"]):
        hay = " ".join(str(x.get(k) or "") for k in ("title", "name", "tagline", "category", "description")).lower()
        if q in hay:
            hits.append({"key": x["key"], "titel": x["title"], "beskrivning": (x.get("tagline") or "")[:160],
                         "kategori": x.get("category"), "butik": x.get("store"), "redan_installerad": x["key"] in installed,
                         "fungerar_har": not x["architectures"] or apps.arch() in x["architectures"]})
    hits.sort(key=lambda h: (q not in h["titel"].lower(), h["titel"]))
    return {"traffar": hits[:15], "antal": len(hits)}


def t_backup(_):
    from . import backup
    s = backup.summary()
    per = []
    for name, v in (backup.state().get("apps") or {}).items():
        per.append({"app": name, "senaste": v.get("last_backup"), "senaste_test": v.get("last_test"), "test_ok": v.get("test_ok")})
    return {**s, "per_app": per}


def t_security(a):
    from . import security
    r = security.check() if a.get("fresh") else security.cached()
    return {"poang": r["score"], "betyg": r["grade"],
            "punkter": [{"niva": {"bad": "rött", "warn": "gult", "ok": "grönt"}[i["level"]], "rubrik": i["title"], "text": i["text"]} for i in r["items"]]}


def t_cost(_):
    from . import cost
    c = cost.summary()
    return {"kr_per_manad": round(c["month_kr"], 1), "kr_per_ar": round(c["year_kr"]), "watt_nu": round(c["watts_now"], 1),
            "elpris_kr_kwh": c["price"], "typ": c["kind_title"], "matt_timmar": c["measured_hours"],
            "per_app": [{"vad": r["title"], "kr_per_manad": round(r["month_kr"], 2)} for r in c["rows"]], "tips": c["tip"]}


def t_snapshots(a):
    from . import snapshots
    app = _app(a.get("app"))
    return {"app": app["name"], "aterstallningspunkter": [
        {"id": s["id"], "skapad": s["created"], "orsak": snapshots.REASONS.get(s.get("reason"), s.get("reason")),
         "version": s.get("version") or s.get("image", "")} for s in snapshots.list_for(app["name"])[:20]]}


def t_files(a):
    from . import files
    r = files.listing(a.get("path") or "/")
    return {"mapp": r["path"], "ledigt_gb": round(r["free"] / 1e9, 1),
            "innehall": [{"namn": e["name"], "mapp": e["dir"], "storlek": e["size"]} for e in r["entries"][:200]],
            "antal": len(r["entries"])}


def t_shares(_):
    from . import shares
    return {"delningar": [{"id": s["token"][:8], "namn": s["name"], "sokvag": s["path"], "gar_ut": s["expires"],
                           "nedladdningar": s["downloads"], "kod": s["has_pin"]} for s in shares.active()]}


def t_find(a):
    from . import finder
    r = finder.search(a.get("query") or "", include_web=bool(a.get("web")))
    out = {"appar": r["apps"], "filer": r["files"], "wizzos_platser": [x["title"] for x in r["places"]],
           "i_appbutiken": [{"key": x["key"], "titel": x["title"]} for x in r["store"]],
           "webben": r["web"].get("results", []) if a.get("web") else "ej sökt (sätt web=true)"}
    if docs_allowed():
        clean = lambda s: (s or "").replace("\x02", "»").replace("\x03", "«")  # noqa: E731
        out["i_dokument"] = [{"sokvag": d["path"], "utdrag": clean(d["snippet"])} for d in r["docs"]]
        out["i_paperless"] = [{"titel": d["title"], "datum": d["created"], "utdrag": clean(d["snippet"])} for d in r["paperless"]]
    else:
        out["i_dokument"] = "Ägaren har inte låtit AI läsa dokumentens innehåll (WizzOS → Inställningar → Dokument)."
    return out


def docs_allowed() -> bool:
    return bool(config.load().get("ai_read_docs", False))


def t_read_doc(a):
    from . import docindex
    if not docs_allowed():
        raise ValueError("Ägaren har inte låtit AI läsa dokumentens innehåll. Det slås på i WizzOS → Inställningar → Dokument.")
    r = docindex.read(a.get("path") or "", 20000)
    return {"sokvag": r["path"], "tecken": r["chars"], "avkortad": r["truncated"], "text": r["text"]}


def t_job(a):
    return _wait(str(a.get("job") or ""), 1)


# --- vanliga saker ---

def t_install_trial(a):
    from . import store, trial
    key = (a.get("key") or "").strip()
    st = store.find(config.load()["stores"], key)
    if not st:
        raise ValueError("Appen finns inte i butiken. Använd search_store och skicka med dess key.")
    minutes = int(a.get("minutes") or 60)
    if minutes not in trial.CHOICES:
        raise ValueError(f"Provtiden ska vara en av {list(trial.CHOICES)} minuter.")
    base = re.sub(r"[^a-z0-9-]+", "-", (st.get("name") or st["title"]).lower()).strip("-")[:28] or "app"
    name, n = base, 2
    while apps.app_dir(name).exists():
        name, n = f"{base}-{n}", n + 1
    job = apps.install(st, name, {}, {}, minutes)
    r = _wait(job, 50)
    r.update(app=name, provtid_minuter=minutes,
             info=f"{st['title']} installeras på prov. Den tas bort spårlöst efter {minutes} minuter om ingen trycker Behåll "
                  "(keep_trial_app). Använd list_apps för adressen.")
    return r


def t_keep(a):
    from . import trial
    app = _app(a.get("app"))
    trial.keep(app["name"])
    return {"app": app["name"], "info": "Appen är nu en vanlig app och ingår i backup och nattliga uppdateringar."}


def t_end_trial(a):
    from . import trial
    app = _app(a.get("app"))
    if not app.get("trial"):
        raise ValueError("Appen är inte på prov – den kan bara tas bort av dig i WizzOS.")
    return {"app": app["name"], **_wait(trial.end(app["name"]), 40)}


def t_app_action(a):
    app = _app(a.get("app"))
    what = a.get("action")
    if what not in ("start", "stop", "restart"):
        raise ValueError("action ska vara start, stop eller restart.")
    apps.action(app["name"], what)
    return {"app": app["name"], "gjort": {"start": "startad", "stop": "stoppad", "restart": "omstartad"}[what]}


def t_update(a):
    app = _app(a.get("app"))
    return {"app": app["name"], **_wait(apps.update(app["name"]), 50),
            "info": "En återställningspunkt sparas först. Mår appen inte bra efteråt backas den automatiskt."}


def t_snapshot_now(a):
    app = _app(a.get("app"))
    return {"app": app["name"], **_wait(apps.snapshot_now(app["name"]), 50)}


def t_backup_now(_):
    from . import backup
    if any(j["title"] == "Backup av alla appar" for j in jobs.running()):
        raise ValueError("En backup pågår redan.")
    if not backup.public_cfg()["configured"]:
        raise ValueError("Backup är inte inställd. Det gör ägaren under Backup i WizzOS.")
    return _wait(backup.run_backup_now(), 20)


def t_share(a):
    from . import proxy, shares
    r = shares.create(a.get("path") or "", int(a.get("days") or 7), str(a.get("pin") or ""), str(a.get("note") or ""), by="AI")
    base = proxy.wizz_url()
    return {"namn": r["name"], "lank": (base + r["url_path"]) if base else r["url_path"], "gar_ut": r["expires"],
            "info": "Länken slutar fungera av sig själv." + ("" if base else " WizzOS saknar egen adress – sätt serverns adress framför /s/…")}


# --- kräver ditt godkännande (nivån "Allt") ---
# Varje verktyg har: förberedelse (kontrollera + beskriv för dig) och utförande (efter ja).

def _fmt_t(t) -> str:
    from datetime import datetime
    return datetime.fromtimestamp(float(t)).strftime("%-d/%-m %H:%M")


def p_install(a):
    from . import store
    st = store.find(config.load()["stores"], (a.get("key") or "").strip())
    if not st:
        raise ValueError("Appen finns inte i butiken. Använd search_store och skicka med dess key.")
    return (f"installera {st['title']} (permanent)",
            f"{st['title']} installeras som en vanlig app – inte på prov – och tas med i backup och nattliga uppdateringar.\n"
            f"{(st.get('tagline') or '')[:200]}")


def x_install(a):
    from . import store
    st = store.find(config.load()["stores"], a["key"])
    base = re.sub(r"[^a-z0-9-]+", "-", (st.get("name") or st["title"]).lower()).strip("-")[:28] or "app"
    name, n = base, 2
    while apps.app_dir(name).exists():
        name, n = f"{base}-{n}", n + 1
    return {"app": name, **_wait(apps.install(st, name, {}, {}, 0), 90)}


def p_uninstall(a):
    app = _app(a.get("app"))
    data = bool(a.get("delete_data"))
    return (f"ta bort {app['title']}" + (" och dess data" if data else ""),
            f"{app['title']} stoppas och tas bort. " + ("Appens data i /DATA/AppData raderas också. " if data else "Datan lämnas kvar. ")
            + "En återställningspunkt sparas först, så den kan fås tillbaka i 30 dagar under \"Nyligen borttagna\".")


def x_uninstall(a):
    app = _app(a.get("app"))
    return {"app": app["name"], **_wait(apps.uninstall(app["name"], bool(a.get("delete_data"))), 90)}


def p_restore(a):
    from . import snapshots
    app = _app(a.get("app"))
    sid = str(a.get("snapshot_id") or "")
    snap = next((s for s in snapshots.list_for(app["name"]) if s["id"] == sid), None)
    if not snap:
        raise ValueError("Återställningspunkten finns inte. Använd list_snapshots.")
    why = snapshots.REASONS.get(snap.get("reason"), snap.get("reason", ""))
    return (f"återställa {app['title']} till {_fmt_t(snap['created'])}",
            f"{app['title']} går tillbaka till hur den var {_fmt_t(snap['created'])} ({why.lower()}). "
            "Läget just nu sparas först som en egen återställningspunkt, så det går att ångra.")


def x_restore(a):
    app = _app(a.get("app"))
    return {"app": app["name"], **_wait(apps.restore_snapshot(app["name"], str(a["snapshot_id"])), 90)}


def p_autoupd(a):
    app = _app(a.get("app"))
    on = bool(a.get("on"))
    return (f"{'slå på' if on else 'stänga av'} nattliga uppdateringar för {app['title']}",
            f"{app['title']} {'uppdateras' if on else 'uppdateras inte längre'} automatiskt på natten.")


def x_autoupd(a):
    from . import updater
    app = _app(a.get("app"))
    return updater.set_auto(app["name"], bool(a.get("on")))


def _share_by_id(sid: str) -> dict:
    from . import shares
    sid = (sid or "").strip()
    hit = [s for s in shares.active() if len(sid) >= 6 and s["token"].startswith(sid)]
    if len(hit) != 1:
        raise ValueError("Delningen finns inte. Använd list_shares för id.")
    return hit[0]


def p_stop_share(a):
    s = _share_by_id(a.get("id"))
    return (f"stoppa delningen av {s['name']}", f"Länken till {s['path']} slutar fungera direkt. Filerna ligger kvar.")


def x_stop_share(a):
    from . import shares
    s = _share_by_id(a.get("id"))
    shares.stop(s["token"])
    return {"stoppad": s["name"]}


def t_approval_status(a):
    from . import approvals
    r = approvals.get(str(a.get("id") or ""))
    if not r:
        raise ValueError("Frågan finns inte.")
    return {"id": r["id"], "fraga": r["title"], "status": STATUS_TXT.get(r["status"], r["status"]),
            "resultat": r["result"], "fel": r["error"]}


STATUS_TXT = {"pending": "väntar på svar", "approved": "godkänd – utförs", "done": "godkänd och utförd",
              "denied": "nekad – ingenting gjordes", "expired": "gick ut efter 15 minuter – ingenting gjordes",
              "failed": "godkänd men misslyckades"}
APPROVAL = {"install_app": (p_install, x_install), "uninstall_app": (p_uninstall, x_uninstall),
            "restore_snapshot": (p_restore, x_restore), "set_auto_update": (p_autoupd, x_autoupd),
            "stop_share": (p_stop_share, x_stop_share)}


def _ask(name: str, args: dict, key: dict) -> dict:
    from . import alerts, approvals
    prep, _ = APPROVAL[name]
    title, detail = prep(args)
    r = approvals.create(key["name"], name, args, title, detail)
    has_phone = bool(alerts.cfg().get("ntfy_url") and alerts.cfg().get("ntfy_topic"))
    r = approvals.wait(r["id"], 75)
    out = {"id": r["id"], "fraga": title, "status": STATUS_TXT.get(r["status"], r["status"]),
           "resultat": r["result"], "fel": r["error"]}
    if r["status"] == "pending":
        out["info"] = ("Frågan har skickats " + ("till ägarens mobil och " if has_phone else "")
                       + "syns på WizzOS startsida. Be användaren svara och använd sedan approval_status med id.")
    return out


def _s(**props):
    req = [k for k, v in props.items() if not v.pop("_opt", False)]
    return {"type": "object", "properties": props, "required": req}


S = {"type": "string"}
TOOLS = [
    # namn, nivå, verktyg, beskrivning, schema, skrivskyddad
    ("wizzos_status", "read", t_status, "Översikt över servern: last, disk, appar, problem, backup, säkerhetsbetyg och elkostnad. Börja här.", _s(), True),
    ("list_apps", "read", t_list_apps, "Lista installerade appar med status, adress, om de är på prov och om ny version finns.", _s(), True),
    ("app_logs", "read", t_app_logs, "Senaste loggraderna för en app (hemligheter är dolda). Bra för felsökning.",
     _s(app={**S, "description": "appens namn från list_apps"}, lines={"type": "integer", "description": "20–400, standard 120", "_opt": True}), True),
    ("search_store", "read", t_search_store, "Sök i appbutiken. Ger key som behövs för att installera.",
     _s(query={**S, "description": "t.ex. 'foto', 'jellyfin', 'lösenord'"}), True),
    ("backup_status", "read", t_backup, "Backupens läge: mål, senaste körning, återställningstester per app.", _s(), True),
    ("security_check", "read", t_security, "Säkerhetskoll i klartext med poäng 0–100 och vad som bör fixas.",
     _s(fresh={"type": "boolean", "description": "kör om kollen nu", "_opt": True}), True),
    ("cost_report", "read", t_cost, "Vad servern kostar i kronor per månad, per app.", _s(), True),
    ("list_snapshots", "read", t_snapshots, "Återställningspunkter för en app (ägaren kan ångra till dem i WizzOS).", _s(app=dict(S)), True),
    ("list_files", "read", t_files, "Lista en mapp under /DATA (bara namn och storlek, inget innehåll).",
     _s(path={**S, "description": "t.ex. '/' eller '/Media'", "_opt": True}), True),
    ("list_shares", "read", t_shares, "Aktiva delningslänkar.", _s(), True),
    ("search_server", "read", t_find, "Sök i hela servern: appar, filnamn under /DATA, WizzOS-inställningar, appbutiken och "
     "– om ägaren tillåtit det – texten i dokument (PDF, Word, Excel …) och Paperless. "
     "Med web=true söks även webben via serverns egen privata sökmotor (SearXNG).",
     _s(query={**S, "description": "t.ex. 'kvitto diskmaskin', 'hyresavtal uppsägningstid', 'backup'"},
        web={"type": "boolean", "description": "sök även på webben och i Paperless", "_opt": True}), True),
    ("read_document", "read", t_read_doc, "Läs texten i ett dokument (sökväg från search_server). Bara om ägaren tillåtit "
     "att AI läser dokument. Max 20 000 tecken.", _s(path={**S, "description": "t.ex. /Dokument/Hyresavtal.pdf"}), True),
    ("job_status", "read", t_job, "Läget för ett bakgrundsjobb som ett annat verktyg startat.", _s(job=dict(S)), True),
    ("install_app_trial", "act", t_install_trial,
     "Installera en app PÅ PROV (30, 60 eller 180 min). Tas bort spårlöst om ingen behåller den. Fråga användaren först.",
     _s(key={**S, "description": "key från search_store"}, minutes={"type": "integer", "enum": [30, 60, 180], "_opt": True}), False),
    ("keep_trial_app", "act", t_keep, "Behåll en provapp så att den blir en vanlig app. Bara när användaren sagt ja.", _s(app=dict(S)), False),
    ("end_trial_app", "act", t_end_trial, "Avsluta ett prov direkt (tar bort provappen spårlöst). Fungerar bara på provappar.", _s(app=dict(S)), False),
    ("app_action", "act", t_app_action, "Starta, stoppa eller starta om en app.",
     _s(app=dict(S), action={"type": "string", "enum": ["start", "stop", "restart"]}), False),
    ("update_app", "act", t_update, "Uppdatera en app säkert: återställningspunkt först, hälsokoll efteråt, backar själv vid fel.", _s(app=dict(S)), False),
    ("create_snapshot", "act", t_snapshot_now, "Spara en återställningspunkt för en app nu.", _s(app=dict(S)), False),
    ("run_backup", "act", t_backup_now, "Starta en backup av alla appar nu.", _s(), False),
    ("create_share_link", "act", t_share, "Skapa en delningslänk till en fil eller mapp som slutar fungera av sig själv.",
     _s(path={**S, "description": "sökväg under /DATA, t.ex. /Media/Foton/2026"}, days={"type": "integer", "enum": [1, 7, 30, 90]},
        pin={**S, "description": "valfri kod 4–8 siffror", "_opt": True}, note={**S, "_opt": True}), False),
    ("install_app", "all", None, "Installera en app PERMANENT (inte på prov). Kräver att ägaren godkänner i mobilen/WizzOS.",
     _s(key={**S, "description": "key från search_store"}), False),
    ("uninstall_app", "all", None, "Ta bort en app. En återställningspunkt sparas först (går att få tillbaka i 30 dagar). "
     "Kräver godkännande.", _s(app=dict(S), delete_data={"type": "boolean", "description": "radera även appens data (standard nej)", "_opt": True}), False),
    ("restore_snapshot", "all", None, "Återställ en app till en återställningspunkt (id från list_snapshots). Kräver godkännande.",
     _s(app=dict(S), snapshot_id=dict(S)), False),
    ("set_auto_update", "all", None, "Slå på/av nattliga uppdateringar för en app. Kräver godkännande.",
     _s(app=dict(S), on={"type": "boolean"}), False),
    ("stop_share", "all", None, "Stoppa en delningslänk (id från list_shares). Kräver godkännande.", _s(id=dict(S)), False),
    ("approval_status", "all", t_approval_status, "Se om ägaren har svarat på en fråga, och resultatet.", _s(id=dict(S)), True),
]
BY_NAME = {t[0]: t for t in TOOLS}

from . import approvals as _approvals  # noqa: E402
_approvals.EXECUTORS.update({k: v[1] for k, v in APPROVAL.items()})

def _instructions(key: dict) -> str:
    from . import i18n
    text = INSTRUCTIONS + f" Den här nyckeln har nivån: {LEVELS[key['level']]}."
    if i18n.server_lang() == "en":
        text += (" NOTE: the owner uses WizzOS in English – answer the user in plain English, even though "
                 "tool results are in Swedish.")
    return text


INSTRUCTIONS = ("Du är kopplad till WizzOS, en hemmaserver. Svara på svenska med vanliga ord. "
                "Börja med wizzos_status. Fråga alltid användaren innan du installerar, stoppar eller uppdaterar något. "
                "Installera nya appar på prov (install_app_trial) så att inget blir kvar om användaren ångrar sig. "
                "Med nivån 'Allt' kan du även be om att ta bort, återställa eller installera permanent – "
                "då får ägaren en fråga i mobilen och inget händer förrän hen svarat ja. Berätta det för användaren.")


def tool_list(level: str) -> list[dict]:
    out = []
    for name, lvl, _, desc, schema, ro in TOOLS:
        if RANK[lvl] > RANK[level]:
            continue
        out.append({"name": name, "description": desc, "inputSchema": schema,
                    "annotations": {"readOnlyHint": ro, "destructiveHint": False, "openWorldHint": False}})
    return out


# ---------- JSON-RPC ----------

def _err(id_, code, msg):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": msg}}


def handle(msg: dict, kid: str, key: dict) -> dict | None:
    mid = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params") or {}
    if mid is None:  # notis – inget svar
        return None
    if method == "initialize":
        v = params.get("protocolVersion")
        from . import alerts
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": v if v in PROTOCOLS else PROTOCOLS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": f"wizzos-{alerts._server_name()}", "title": "WizzOS", "version": __version__},
            "instructions": _instructions(key)}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": tool_list(key["level"])}}
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        t = BY_NAME.get(name)
        if not t:
            return _err(mid, -32602, f"Okänt verktyg: {name}")
        if RANK[t[1]] > RANK[key["level"]]:
            diary(key["name"], name, args, False, f"Nekad – nyckeln har nivån {LEVELS[key['level']]}.")
            return {"jsonrpc": "2.0", "id": mid, "result": {"isError": True, "content": [{"type": "text",
                    "text": f"Den här nyckeln har nivån '{LEVELS[key['level']]}' och får inte göra det. "
                            f"Ägaren kan ge nivån '{LEVELS[t[1]]}' i WizzOS → Inställningar → AI-koppling."}]}}
        _count(kid)
        try:
            res = _ask(name, args, key) if name in APPROVAL else t[2](args)
            text = json.dumps(res, ensure_ascii=False, default=str)
            diary(key["name"], name, args, True, _summary(name, res))
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}],
                                                             "structuredContent": res, "isError": False}}
        except (ValueError, RuntimeError, FileNotFoundError, PermissionError) as e:
            diary(key["name"], name, args, False, str(e))
            return {"jsonrpc": "2.0", "id": mid, "result": {"isError": True, "content": [{"type": "text", "text": str(e)}]}}
        except Exception as e:  # noqa: BLE001
            diary(key["name"], name, args, False, f"Internt fel: {type(e).__name__}")
            return {"jsonrpc": "2.0", "id": mid, "result": {"isError": True,
                    "content": [{"type": "text", "text": f"Något gick fel i WizzOS ({type(e).__name__})."}]}}
    if method in ("resources/list", "prompts/list"):
        return {"jsonrpc": "2.0", "id": mid, "result": {method.split("/")[0]: []}}
    return _err(mid, -32601, f"Metoden finns inte: {method}")


def _summary(name: str, res: dict) -> str:
    if name in APPROVAL:
        return f"Frågade om att {res.get('fraga')} – {res.get('status')}."
    if name == "approval_status":
        return f"Kollade svaret: {res.get('fraga')} – {res.get('status')}."
    if name == "install_app_trial":
        return f"Installerade {res.get('app')} på prov ({res.get('provtid_minuter')} min) – {res.get('state')}."
    if name == "app_action":
        return f"{res.get('app')} {res.get('gjort')}."
    if name in ("update_app", "create_snapshot", "run_backup", "end_trial_app"):
        what = {"update_app": "Uppdatering av", "create_snapshot": "Återställningspunkt för",
                "run_backup": "Backup", "end_trial_app": "Avslutade provet av"}[name]
        return f"{what} {res.get('app', '')}: {res.get('state')}.".replace("  ", " ")
    if name == "keep_trial_app":
        return f"Behöll {res.get('app')}."
    if name == "create_share_link":
        return f"Delade {res.get('namn')}."
    return "Läste " + {"wizzos_status": "serverns status", "list_apps": "listan över appar", "app_logs": "loggar",
                       "search_store": "appbutiken", "backup_status": "backupen", "security_check": "säkerhetskollen",
                       "cost_report": "kostnaden", "list_snapshots": "återställningspunkter", "list_files": "en mapplista",
                       "list_shares": "delningarna", "job_status": "ett jobb", "search_server": "sökningen i servern",
                       "read_document": "ett dokument"}.get(name, name) + "."
