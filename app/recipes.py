"""WizzOS-paket: appar som kommer färdiginställda.

Ett WizzOS-paket är en vanlig docker-compose-fil i CasaOS-format med ett extra
block `x-wizzos` (se docs/wizzos-paket.md). Blocket säger vad WizzOS ska göra
utöver en vanlig installation: skapa hemliga nycklar, mappar och färdiga
inställningsfiler, visa "Så kommer du igång" som ett mål, köra något före
backupen och koppla appen till WizzOS (t.ex. Paperless till sökningen).

WizzOS egna paket ligger i app/paket/Apps/<namn>/docker-compose.yml och syns i
Appbutiken som butiken "wizzos". Samma block fungerar i vilken butik som helst.
"""
from __future__ import annotations

import os
import re
import secrets
import string
from pathlib import Path

from . import config

PKG_DIR = Path(__file__).parent / "paket"
STORE_ID = "wizzos"
_cache: dict[str, dict] | None = None
VAR = re.compile(r"\{\{\s*([A-Z][A-Z0-9_]*)\s*\}\}")


def _load() -> dict[str, dict]:
    global _cache
    if _cache is None:
        from . import store
        out = {}
        for f in sorted(PKG_DIR.glob("Apps/*/docker-compose.yml")):
            app = store.parse_compose(f.read_text("utf-8"), STORE_ID, f.parent.name)
            if app:
                out[app["key"]] = app
        _cache = out
    return _cache


def all_apps() -> list[dict]:
    return list(_load().values())


def find(key: str) -> dict | None:
    return _load().get(key)


def block(app: dict) -> dict:
    b = app.get("wizzos")
    return b if isinstance(b, dict) else {}


def text(value, lang: str | None = None) -> str:
    """Välj språk ur {sv_SE: …, en_US: …} (eller en vanlig sträng)."""
    from . import i18n, store
    lang = lang or i18n.current()
    return store.pick(value, ("en_US", "en_GB", "en", "sv_SE") if lang == "en" else
                      ("sv_SE", "en_US", "en_GB", "en"))


# ---------- installation ----------

def context(app: dict, name: str) -> dict:
    """Värden som {{NAMN}} byts mot: hemliga nycklar, appens adress, språk …"""
    from . import i18n, proxy
    b = block(app)
    s = config.load()
    lang = i18n.server_lang()
    px = proxy.settings()
    alphabet = string.ascii_letters + string.digits
    ctx = {"APP_ID": name, "PUID": str(s["puid"]), "PGID": str(s["pgid"]), "TZ": s["tz"],
           "LANG": lang, "LOCALE": "sv-SE" if lang == "sv" else "en-GB",
           "APP_URL": f"https://{proxy.default_sub(name)}.{px['domain']}" if px["enabled"] and px["domain"] else ""}
    for k, n in (b.get("secrets") or {}).items():
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", str(k)):
            ctx[str(k)] = "".join(secrets.choice(alphabet) for _ in range(max(12, min(int(n or 32), 128))))
    return ctx


def fill(value: str, ctx: dict) -> str:
    return VAR.sub(lambda m: ctx.get(m.group(1), m.group(0)), str(value))


def _path(p: str, name: str) -> Path | None:
    """Relativ sökväg = under /DATA/AppData/<app>. Absolut = måste ligga under /DATA."""
    root = config.DATA_ROOT.resolve()
    if p == "/DATA" or p.startswith("/DATA/"):
        p = str(root) + p[5:]
    full = (Path(p) if os.path.isabs(p) else root / "AppData" / name / p).resolve()
    if full == root or root not in full.parents or config.HOME.resolve() in (full, *full.parents):
        return None
    return full


def _own(p: Path, owner: str) -> None:
    s = config.load()
    uid, gid = (0, 0) if owner == "root" else (int(s["puid"]), int(s["pgid"]))
    try:
        os.chown(p, uid, gid)
    except (OSError, AttributeError):  # AttributeError: Windows (bara vid utveckling)
        pass


def prepare(app: dict, name: str, log, ctx: dict | None = None) -> None:
    """Före första start: mappar, färdiga filer (skrivs aldrig över) och wizzos.env."""
    b = block(app)
    if not b:
        return
    ctx = ctx or context(app, name)
    for d in b.get("dirs") or []:
        d = d if isinstance(d, dict) else {"path": d}
        p = _path(str(d.get("path", "")), name)
        if not p:
            log(f"Hoppar över mappen {d.get('path')} (måste ligga under /DATA).")
            continue
        new = not p.exists()
        p.mkdir(parents=True, exist_ok=True)
        if new or d.get("owner") == "root":
            _own(p, d.get("owner", "app"))
    for f in b.get("files") or []:
        p = _path(str(f.get("path", "")), name)
        if not p or p.exists():
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(fill(f.get("content", ""), ctx), "utf-8")
        _own(p, f.get("owner", "app"))
        if f.get("mode"):
            try:
                os.chmod(p, int(str(f["mode"]), 8))
            except (OSError, ValueError):
                pass
    # Miljövariabler till appen (env_file: wizzos.env). Utan env-lista skickas nycklarna som de heter.
    if b.get("env") is not None:
        env = {str(k): fill(v, ctx) for k, v in (b.get("env") or {}).items()}
    else:
        env = {k: ctx[k] for k in (b.get("secrets") or {}) if k in ctx}
    if env:
        from . import apps
        envf = apps.app_dir(name) / "wizzos.env"
        envf.write_text("".join(f"{k}={v}\n" for k, v in env.items()), "utf-8")
        os.chmod(envf, 0o600)


def after_install(app: dict, name: str, meta: dict, log, ctx: dict) -> None:
    """Efter start: meddelanden till ägaren och kopplingar till WizzOS."""
    b = block(app)
    if not b:
        return
    if b.get("link") == "paperless":
        _link_paperless(name, meta, log)
    for line in b.get("after_install") or []:
        log(fill(text(line), ctx))


def backup_before(app: dict) -> dict | None:
    """{service, command} som körs i appen före backupen – då behöver den inte stoppas."""
    bb = (block(app).get("backup") or {}).get("before")
    if isinstance(bb, dict) and bb.get("service") and bb.get("command"):
        return {"service": str(bb["service"]), "command": str(bb["command"])}
    return None


STEP_KEYS = ("restart", "restart_service", "wait", "exec")


def playbooks(app: dict) -> list[dict]:
    """Paketets egna "så lagar du mig"-regler (x-wizzos.playbooks), kontrollerade.

    - when: unhealthy            # när appen inte mår bra (det enda som stöds än)
      log: "database is locked"  # valfritt: bara om loggen innehåller detta (regex)
      steps:                     # högst 8: restart | restart_service: <tjänst> | wait: <sek ≤ 300>
        - restart_service: redis #           | exec: {service, command}  (≤ 300 s)
      explain: {sv_SE: "…", en_US: "…"}   # visas för ägaren om stegen inte räckte
    """
    import re as _re
    out = []
    for pb in (block(app).get("playbooks") or [])[:10]:
        if not isinstance(pb, dict) or pb.get("when", "unhealthy") != "unhealthy":
            continue
        steps = []
        for st in (pb.get("steps") or [])[:8]:
            if st == "restart":
                steps.append({"restart": True})
            elif isinstance(st, dict) and len(st) == 1 and next(iter(st)) in STEP_KEYS:
                k, v = next(iter(st.items()))
                if k == "restart_service" and isinstance(v, str) and _re.match(r"^[\w.-]+$", v):
                    steps.append({k: v})
                elif k == "wait" and isinstance(v, (int, float)) and 0 < v <= 300:
                    steps.append({k: int(v)})
                elif k == "exec" and isinstance(v, dict) and v.get("service") and v.get("command"):
                    steps.append({k: {"service": str(v["service"]), "command": str(v["command"])[:500]}})
        if not steps:
            continue
        log = str(pb.get("log") or "")
        try:
            _re.compile(log)
        except _re.error:
            continue
        explain = pb.get("explain")
        out.append({"when": "unhealthy", "log": log, "steps": steps,
                    "explain": text(explain) if explain else ""})
    return out


def _link_paperless(name: str, meta: dict, log) -> None:
    import subprocess
    import time
    from . import apps, paperless
    if paperless.cfg().get("token"):
        log("Paperless är redan kopplad till sökningen – ändrar inget.")
        return
    log("Väntar på att Paperless ska starta första gången (kan ta några minuter) ...")
    svc = meta.get("main") or "paperless"
    deadline = time.time() + 600
    token = ""
    while time.time() < deadline:
        r = subprocess.run(apps._compose(name, "exec", "-T", svc, "python3", "manage.py",
                                         "drf_create_token", "admin"), capture_output=True, text=True)
        m = re.search(r"token ([0-9a-f]{40})", r.stdout)
        if m:
            token = m.group(1)
            break
        time.sleep(15)
    if not token:
        log("Kunde inte koppla Paperless till sökningen automatiskt. Gör det under Inställningar → Dokument.")
        return
    host = config.load().get("bind_ip") or "172.17.0.1"
    try:
        paperless.save(f"http://{host}:{meta.get('port')}", token)
        log("Paperless är kopplad till WizzOS sökning (Ctrl K).")
    except Exception as e:  # noqa: BLE001
        log(f"Kunde inte koppla Paperless till sökningen: {e}")


def on_removed(meta: dict, log) -> None:
    """Appen tas bort: släpp kopplingar som pekar på just den (t.ex. sökningen i Paperless)."""
    from . import paperless
    app = find(meta.get("store_key", "")) or {}
    if block(app).get("link") == "paperless" and meta.get("port") and \
            paperless.cfg().get("url", "").endswith(f":{meta['port']}"):
        paperless.save("", None)
        log("Kopplingen mellan Paperless och sökningen är borttagen.")


# ---------- mål ("Vad vill du göra?") ----------

def goals() -> list[dict]:
    out = []
    for app in all_apps():
        g = block(app).get("goal")
        if not isinstance(g, dict) or not g.get("id"):
            continue
        out.append({
            "id": str(g["id"]), "emoji": str(g.get("emoji", "📦")), "title": text(g.get("title")),
            "pitch": text(g.get("pitch")), "app": app["key"], "app_title": app["title"],
            "min_ram_gb": float(g.get("min_ram_gb") or 0), "needs_https": bool(g.get("needs_https")),
            "disk": text(g.get("disk")), "steps": [text(s) for s in g.get("steps") or []],
            "mobile": g.get("mobile") or {}, "package": True})
    return out


def search_app() -> dict | None:
    """Den installerade sökmotorn (SearXNG-paketet), om någon."""
    from . import apps
    hits = [a for a in apps.installed()["apps"] if a.get("store_key") == "wizzos/searxng"]
    hits.sort(key=lambda a: (bool(a.get("trial")), a.get("status") != "running"))
    return hits[0] if hits else None
