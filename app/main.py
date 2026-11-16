"""WizzOS – webbserver och API."""
from __future__ import annotations

import asyncio
import re
from contextlib import nullcontext
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (__version__, ai, alerts, approvals, apps, auth, backup, card, config, cost, docindex, files, finder, goals, homeassistant, jobs,
               legacy, mail, mcp, oauth, paperless, proxy, security, selfupdate, shares, snapshots,
               letter, move, nestward, store, system, trial, updater, users)

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="WizzOS", docs_url=None, redoc_url=None, openapi_url=None)
OPEN_PATHS = {"/api/state", "/api/login", "/api/setup"}


# Det här får familj och gäster använda – allt annat är bara för ägaren och admin.
_LIMITED = {("GET", "/api/me"), ("GET", "/api/me/apps"), ("POST", "/api/me/password"),
            ("POST", "/api/logout")}
_FAMILY_RE = re.compile(r"^/api/(files(/[a-z]+)?|trash|trash/[\w-]+(/restore)?)$")


def _limited_ok(user: dict, method: str, path: str) -> bool:
    if (method, path) in _LIMITED:
        return True
    if user["role"] == "family" and _FAMILY_RE.match(path):
        return True
    return False


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in OPEN_PATHS:
        user = auth.current(request.cookies.get(auth.COOKIE))
        if not user:
            return JSONResponse({"detail": "Inte inloggad"}, status_code=401)
        request.scope["wizzos_user"] = user
        if user["role"] != "admin" and not _limited_ok(user, request.method, path):
            return JSONResponse({"detail": "Det här får bara den som sköter servern göra."}, status_code=403)
        if user["id"] == "owner":
            legacy.touch()  # du använder WizzOS = du är här
    # Enkelt skydd mot att andra sajter skickar formulär hit.
    if request.method not in ("GET", "HEAD") and path.startswith("/api/") \
            and request.headers.get("x-wizzos") != "1":
        return JSONResponse({"detail": "Saknar huvud"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.exception_handler(ValueError)
async def value_error(_, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(RuntimeError)
async def runtime_error(_, exc: RuntimeError):
    return JSONResponse({"detail": str(exc)}, status_code=500)


@app.on_event("startup")
async def startup():
    config.ensure_dirs()
    if not config.load().get("secret"):
        import secrets
        config.update(secret=secrets.token_hex(32))
    snapshots.start_cleaner()
    files.start_cleaner()
    trial.start_reaper()
    nestward.start_writer()
    letter.start_scheduler()
    updater.start_scheduler()
    homeassistant.start_pusher()
    cost.start_meter()
    shares.start_cleaner()
    legacy.start_watch()
    finder.start_indexer()
    docindex.start_indexer()
    selfupdate.announce_on_start()
    backup.start_scheduler()
    alerts.start_monitor()
    proxy.safe_apply()
    missing = [s for s in config.load()["stores"] if not store.load_store(s["id"])]
    if missing:
        refresh_job(missing)


def refresh_job(stores: list[dict]) -> str:
    def work(log):
        for s in stores:
            log(f"Hämtar {s['name']} ...")
            r = asyncio.run(store.refresh(s))
            log(f"{s['name']}: {r['count']} appar")
    return jobs.start("Uppdaterar appbutiker", work)


# ---------- inloggning ----------

class Password(BaseModel):
    password: str


class LoginIn(BaseModel):
    password: str
    user: str = ""


def _user(request: Request) -> dict:
    return request.scope.get("wizzos_user") or users.owner()


def _fs(request: Request):
    """Familjemedlemmar ser bara sin egen mapp."""
    u = _user(request)
    if u["role"] == "admin":
        return nullcontext()
    root = users.folder_path(u)
    if not root:
        raise HTTPException(403, "Du har ingen egen mapp.")
    return files.scoped(root)


def _login_response(data: dict, request: Request, uid: str = "owner") -> JSONResponse:
    resp = JSONResponse(data)
    resp.set_cookie(auth.COOKIE, auth.make_token(uid), max_age=auth.SESSION_DAYS * 86400,
                    httponly=True, samesite="strict",
                    secure=request.url.scheme == "https")
    return resp


@app.get("/api/state")
def state(request: Request):
    has = auth.has_password()
    return {"setup": not has,
            "logged_in": auth.valid_token(request.cookies.get(auth.COOKIE)),
            "people": users.login_choices() if has else [],
            "version": __version__}


@app.post("/api/setup")
def setup(body: Password, request: Request):
    if auth.has_password():
        raise HTTPException(403, "Lösenord är redan valt.")
    if len(body.password) < 8:
        raise HTTPException(400, "Välj minst 8 tecken.")
    auth.set_password(body.password)
    config.update(welcome_pending=True)  # visa välkomstguiden efter första inloggningen
    return _login_response({"ok": True}, request)


@app.get("/api/welcome")
def welcome_get():
    return {"pending": bool(config.load().get("welcome_pending"))}


@app.post("/api/welcome/done")
def welcome_done():
    config.update(welcome_pending=False)
    return {"ok": True}


@app.post("/api/login")
def login(body: LoginIn, request: Request):
    ip = request.client.host if request.client else "?"
    if auth.too_many_failures(ip):
        raise HTTPException(429, "För många försök. Vänta fem minuter.")
    uid = body.user or "owner"
    ok = auth.check_password(body.password) if uid == "owner" else users.check_password(uid, body.password)
    if not ok:
        auth.note_failure(ip)
        raise HTTPException(401, "Fel lösenord.")
    users.note_login(uid)
    return _login_response({"ok": True}, request, uid)


# ---------- familjekonton ----------

@app.get("/api/me")
def me(request: Request):
    u = _user(request)
    return {"id": u["id"], "name": u["name"], "role": u["role"], "emoji": u.get("emoji", ""),
            "owner": u["id"] == "owner", "files": u["role"] == "admin" or bool(u.get("folder"))}


@app.get("/api/me/apps")
def me_apps(request: Request):
    return {"apps": users.visible_apps(_user(request))}


class MyPassword(BaseModel):
    current: str
    password: str


@app.post("/api/me/password")
def me_password(body: MyPassword, request: Request):
    u = _user(request)
    if u["id"] == "owner":
        raise HTTPException(400, "Ägaren byter lösenord under Inställningar.")
    if not users.check_password(u["id"], body.current):
        raise HTTPException(400, "Nuvarande lösenord stämmer inte.")
    users.update(u["id"], password=body.password)
    return _login_response({"ok": True}, request, u["id"])


class UserIn(BaseModel):
    name: str | None = None
    role: str | None = None
    password: str | None = None
    apps: list[str] | None = None
    emoji: str | None = None


@app.get("/api/users")
def users_list():
    return {"users": users.listing(), "roles": users.ROLES}


@app.post("/api/users")
def users_create(body: UserIn):
    return users.create(body.name or "", body.role or "family", body.password or "", body.apps, body.emoji or "")


@app.put("/api/users/{uid}")
def users_update(uid: str, body: UserIn, request: Request):
    if uid == _user(request)["id"] and body.role and body.role != "admin":
        raise HTTPException(400, "Du kan inte ta bort din egen admin-roll.")
    return users.update(uid, body.name, body.role, body.apps, body.emoji, body.password)


@app.delete("/api/users/{uid}")
def users_delete(uid: str, request: Request):
    if uid == _user(request)["id"]:
        raise HTTPException(400, "Du kan inte ta bort dig själv.")
    users.remove(uid)
    return {"ok": True}


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE)
    return resp


class NewPassword(BaseModel):
    current: str
    password: str


@app.post("/api/password")
def change_password(body: NewPassword, request: Request):
    if _user(request)["id"] != "owner":
        raise HTTPException(403, "Bara ägaren kan byta ägarens lösenord.")
    if not auth.check_password(body.current):
        raise HTTPException(400, "Nuvarande lösenord stämmer inte.")
    if len(body.password) < 8:
        raise HTTPException(400, "Välj minst 8 tecken.")
    auth.set_password(body.password)
    return _login_response({"ok": True}, request)


# ---------- system ----------

@app.get("/api/system")
def get_system():
    ok, version = apps.docker_ok()
    data = system.stats()
    data["docker"] = {"ok": ok, "version": version}
    data["arch"] = apps.arch()
    data["jobs"] = jobs.running()
    data["backup"] = backup.summary()
    data["problems"] = alerts.public_cfg()["active"]
    return data


# ---------- appbutik ----------

@app.get("/api/store")
def store_list():
    s = config.load()
    items = [store.summary(a) for a in store.all_apps(s["stores"])]
    installed = {a.get("store_key") for a in apps.installed()["apps"]}
    for a in items:
        a["installed"] = a["key"] in installed
        a["supported"] = not a["architectures"] or apps.arch() in a["architectures"]
    cats: dict[str, int] = {}
    for a in items:
        cats[a["category"]] = cats.get(a["category"], 0) + 1
    stores_info = []
    for st in s["stores"]:
        d = store.load_store(st["id"])
        stores_info.append({**st, "count": len(d["apps"]) if d else 0,
                            "updated": d["updated"] if d else 0})
    return {"apps": items, "categories": sorted(cats.items(), key=lambda c: -c[1]),
            "stores": stores_info}


@app.get("/api/store/app")
def store_app(key: str):
    a = store.find(config.load()["stores"], key)
    if not a:
        raise HTTPException(404, "Appen hittades inte.")
    form = apps.install_form(a["compose"], a["name"])
    return {**a, "form": form, "supported": not a["architectures"] or apps.arch() in a["architectures"]}


@app.post("/api/store/refresh")
def store_refresh():
    return {"job": refresh_job(config.load()["stores"])}


class StoreIn(BaseModel):
    name: str
    url: str


@app.post("/api/stores")
def add_store(body: StoreIn):
    url = body.url.strip()
    if not re.match(r"^https://", url):
        raise HTTPException(400, "Adressen måste börja med https://")
    s = config.load()
    if any(x["url"] == url for x in s["stores"]):
        raise HTTPException(400, "Den butiken finns redan.")
    sid = re.sub(r"[^a-z0-9]+", "-", (body.name or url).lower()).strip("-")[:30] or "butik"
    while any(x["id"] == sid for x in s["stores"]):
        sid += "-2"
    entry = {"id": sid, "name": body.name.strip() or sid, "url": url}
    config.update(stores=s["stores"] + [entry])
    return {"job": refresh_job([entry])}


@app.delete("/api/stores/{sid}")
def remove_store(sid: str):
    s = config.load()
    config.update(stores=[x for x in s["stores"] if x["id"] != sid])
    store.forget(sid)
    return {"ok": True}


@app.get("/api/stores/suggested")
def suggested():
    have = {x["url"] for x in config.load()["stores"]}
    return [x for x in config.SUGGESTED_STORES if x["url"] not in have]


# ---------- installera ----------

class InstallIn(BaseModel):
    key: str
    name: str
    ports: dict[str, str] = {}
    envs: dict[str, str] = {}
    trial_minutes: int = 0


@app.post("/api/install")
def install(body: InstallIn):
    a = store.find(config.load()["stores"], body.key)
    if not a:
        raise HTTPException(404, "Appen hittades inte.")
    minutes = body.trial_minutes
    if minutes and minutes not in trial.CHOICES:
        raise HTTPException(400, "Ogiltig provtid.")
    return {"job": apps.install(a, body.name, body.ports, body.envs, minutes)}


class TrialExtendIn(BaseModel):
    minutes: int = 60


@app.post("/api/apps/{name}/trial/keep")
def trial_keep(name: str):
    return trial.keep(name)


@app.post("/api/apps/{name}/trial/extend")
def trial_extend(name: str, body: TrialExtendIn):
    return trial.extend(name, body.minutes)


@app.post("/api/apps/{name}/trial/end")
def trial_end(name: str):
    return {"job": trial.end(name)}


class CustomIn(BaseModel):
    name: str
    title: str = ""
    compose: str
    port: str = ""
    icon: str = ""


@app.post("/api/custom")
def custom(body: CustomIn):
    return {"job": apps.install_custom(body.name.strip().lower(), body.title, body.compose,
                                       body.port, body.icon)}


# ---------- mina appar ----------

@app.get("/api/apps")
def list_apps():
    return apps.installed()


@app.post("/api/apps/{name}/{what}")
def app_action(name: str, what: str):
    return {"job": apps.action(name, what)}


@app.delete("/api/apps/{name}")
def app_remove(name: str, data: bool = False):
    return {"job": apps.uninstall(name, data)}


@app.get("/api/apps/{name}/logs", response_class=PlainTextResponse)
def app_logs(name: str):
    return apps.logs(name)


@app.get("/api/apps/{name}/compose", response_class=PlainTextResponse)
def app_compose(name: str):
    return apps.read_compose(name)


class ComposeIn(BaseModel):
    compose: str


@app.put("/api/apps/{name}/compose")
def app_compose_save(name: str, body: ComposeIn):
    return {"job": apps.save_compose(name, body.compose)}


class AddressIn(BaseModel):
    sub: str = ""


@app.put("/api/apps/{name}/address")
def app_address(name: str, body: AddressIn):
    return apps.set_address(name, body.sub)


# ---------- ångra / återställningspunkter ----------

@app.get("/api/apps/{name}/snapshots")
def app_snapshots(name: str):
    return {"snapshots": snapshots.list_for(name), "reasons": snapshots.REASONS,
            "keep_days": snapshots.KEEP_DAYS}


@app.post("/api/apps/{name}/snapshots")
def app_snapshot_now(name: str):
    return {"job": apps.snapshot_now(name)}


@app.post("/api/snapshots/{name}/{sid}/restore")
def snapshot_restore(name: str, sid: str):
    return {"job": apps.restore_snapshot(name, sid)}


@app.delete("/api/snapshots/{name}/{sid}")
def snapshot_delete(name: str, sid: str):
    snapshots.delete(name, sid)
    return {"ok": True}


@app.get("/api/removed")
def removed():
    return snapshots.removed_apps()


@app.post("/api/containers/{cid}/{what}")
def container_action(cid: str, what: str):
    apps.container_action(cid, what)
    return {"ok": True}


@app.get("/api/containers/{cid}/logs", response_class=PlainTextResponse)
def container_logs(cid: str):
    return apps.container_logs(cid)


# ---------- jobb ----------

@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(404, "Jobbet finns inte.")
    return j


# ---------- larm ----------

@app.get("/api/alerts")
def alerts_get():
    return alerts.public_cfg()


class AlertsIn(BaseModel):
    enabled: bool | None = None
    ntfy_url: str | None = None
    ntfy_topic: str | None = None
    ntfy_token: str | None = None
    webhook_url: str | None = None
    disk_percent: int | None = None


@app.put("/api/alerts")
def alerts_put(body: AlertsIn):
    data = body.model_dump()
    for k in ("ntfy_url", "webhook_url"):
        if data.get(k) and not re.match(r"^https?://", data[k].strip()):
            raise HTTPException(400, "Adresser måste börja med http:// eller https://")
    return alerts.save(data)


@app.post("/api/alerts/test")
def alerts_test():
    alerts.send("Testlarm", "Det här är ett test. Kommer det fram så fungerar larmen. 👍", 3,
                "white_check_mark")
    return {"ok": True}


# ---------- AI-hjälp ----------

@app.get("/api/ai")
def ai_get():
    return ai.public_cfg()


class AiSettingsIn(BaseModel):
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    cf_id: str | None = None
    cf_secret: str | None = None
    clear_cf: bool = False
    enabled: bool = True


@app.put("/api/ai")
def ai_put(body: AiSettingsIn):
    return ai.save(body.model_dump())


@app.post("/api/ai/test")
def ai_test():
    return {"reply": ai.test()}


class AiChatIn(BaseModel):
    messages: list[dict]
    app: str | None = None
    extra: str | None = None


@app.post("/api/ai/chat")
def ai_chat(body: AiChatIn):
    return ai.chat(body.messages, body.app, body.extra)


# ---------- backup ----------

@app.get("/api/backup")
def backup_get():
    st = backup.state().get("apps", {})
    rows = []
    for a in apps.installed()["apps"]:
        rows.append({"name": a["name"], "title": a["title"], "icon": a.get("icon", ""),
                     **st.get(a["name"], {})})
    return {"config": backup.public_cfg(), "summary": backup.summary(), "apps": rows}


@app.get("/api/backup/key")
def backup_pubkey():
    return {"public_key": backup.public_key()}


class BackupSetupIn(BaseModel):
    type: str
    path: str | None = None
    sftp_user: str | None = None
    sftp_host: str | None = None
    sftp_port: str | None = None
    sftp_path: str | None = None
    s3_endpoint: str | None = None
    s3_bucket: str | None = None
    s3_key: str | None = None
    s3_secret: str | None = None
    time: str | None = None
    max_gb: str | None = None
    existing_password: str | None = None


@app.post("/api/backup/setup")
def backup_setup(body: BackupSetupIn):
    data = body.model_dump()
    return {"job": jobs.start("Ställer in backup", lambda log: backup.setup(data, log))}


class BackupSettingsIn(BaseModel):
    time: str | None = None
    enabled: bool | None = None
    max_gb: float | None = None


@app.put("/api/backup/settings")
def backup_settings(body: BackupSettingsIn):
    ch = {k: v for k, v in body.model_dump().items() if v is not None}
    if "time" in ch and not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", ch["time"]):
        raise HTTPException(400, "Skriv tiden som 03:30.")
    if "max_gb" in ch and not 0 <= ch["max_gb"] <= 100000:
        raise HTTPException(400, "Ogiltigt tak.")
    backup.save_cfg(**ch)
    return backup.public_cfg()


class RevealIn(BaseModel):
    password: str


@app.post("/api/backup/reveal")
def backup_reveal(body: RevealIn, request: Request):
    ip = request.client.host if request.client else "?"
    if auth.too_many_failures(ip):
        raise HTTPException(429, "För många försök. Vänta fem minuter.")
    if not auth.check_password(body.password):
        auth.note_failure(ip)
        raise HTTPException(401, "Fel lösenord.")
    return {"key": backup.cfg().get("password", "")}


@app.post("/api/backup/run")
def backup_run():
    if any(j["title"] == "Backup av alla appar" for j in jobs.running()):
        raise HTTPException(400, "En backup pågår redan.")
    return {"job": backup.run_backup_now()}


@app.post("/api/backup/apps/{name}/{what}")
def backup_app_action(name: str, what: str):
    apps.app_dir(name)
    if what == "backup":
        return {"job": apps.guarded_job(name, f"Backup av {name}",
                                        lambda log: backup.backup_app(name, log))}
    if what == "test":
        return {"job": apps.guarded_job(name, f"Återställningstest av {name}",
                                        lambda log: backup.restore_test(name, log))}
    raise HTTPException(400, "Okänd åtgärd")


@app.get("/api/backup/apps/{name}/snapshots")
def backup_app_snapshots(name: str):
    apps.app_dir(name)
    return backup.list_snapshots(name)


@app.get("/api/backup/available")
def backup_available():
    return backup.apps_in_backup()


class BackupRestoreIn(BaseModel):
    snapshot: str


@app.post("/api/backup/apps/{name}/restore")
def backup_app_restore(name: str, body: BackupRestoreIn):
    apps.app_dir(name)

    def work(log):
        backup.restore_app(name, body.snapshot, log)
        ok, why = apps.wait_healthy(name, log, stable=15)
        log("Klart! Appen är återställd från backupen." if ok else f"Återställd, men mår inte bra: {why}")
        return {"app": name}
    return {"job": apps.guarded_job(name, f"Återställer {name} från backup", work)}


# ---------- egen adress (https) ----------

@app.get("/api/proxy")
def proxy_get():
    return {**proxy.settings(), "wizz_url": proxy.wizz_url()}


class ProxyIn(BaseModel):
    domain: str
    ip: str
    token: str = ""


@app.post("/api/proxy")
def proxy_enable(body: ProxyIn):
    return {"job": jobs.start("Slår på egen adress",
                              lambda log: proxy.enable(body.domain, body.ip, body.token, log))}


@app.delete("/api/proxy")
def proxy_disable():
    return {"job": jobs.start("Stänger av egen adress", proxy.disable)}


@app.get("/api/proxy/logs", response_class=PlainTextResponse)
def proxy_logs():
    return proxy.proxy_logs()


# ---------- filer ----------

from urllib.parse import quote  # noqa: E402


def _disposition(kind: str, name: str) -> str:
    ascii_name = name.encode("ascii", "replace").decode().replace('"', "_").replace("?", "_")
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


class PathsIn(BaseModel):
    paths: list[str]


class MkdirIn(BaseModel):
    path: str
    name: str


class RenameIn(BaseModel):
    path: str
    name: str


class MoveIn(BaseModel):
    paths: list[str]
    dest: str
    as_copy: bool = False


class TextIn(BaseModel):
    path: str
    text: str
    mtime: int | None = None


@app.get("/api/files")
def files_list(request: Request, path: str = "/"):
    with _fs(request):
        return files.listing(path)


@app.get("/api/files/download")
def files_download(request: Request, path: str, inline: bool = False):
    with _fs(request):
        p = files.resolve(path)
        if p.is_dir():
            return StreamingResponse(files.zip_stream(p), media_type="application/zip",
                                     headers={"Content-Disposition": _disposition("attachment", p.name + ".zip")})
        if not p.is_file():
            raise HTTPException(404, "Filen finns inte.")
        show = files.safe_inline_type(p) if inline else None
        headers = {"Content-Disposition": _disposition("inline" if show else "attachment", p.name),
                   # Visade filer får aldrig köra något i WizzOS.
                   "Content-Security-Policy": "default-src 'none'; img-src 'self'; media-src 'self'; style-src 'unsafe-inline'; sandbox",
                   "Cache-Control": "private, no-cache"}
        return FileResponse(p, media_type=show or "application/octet-stream", headers=headers)


@app.put("/api/files/upload")
async def files_upload(request: Request, dir: str, name: str, overwrite: bool = False):
    with _fs(request):
        try:
            up = files.Upload(dir, name, overwrite)
        except FileExistsError:
            raise HTTPException(409, f"{name} finns redan.")
        try:
            async for chunk in request.stream():
                if chunk:
                    await asyncio.to_thread(up.write, chunk)
        except BaseException:
            up.abort()
            raise
        return up.finish()


@app.post("/api/files/mkdir")
def files_mkdir(body: MkdirIn, request: Request):
    with _fs(request):
        return {"path": files.mkdir(body.path, body.name)}


@app.post("/api/files/rename")
def files_rename(body: RenameIn, request: Request):
    with _fs(request):
        return {"path": files.rename(body.path, body.name)}


@app.post("/api/files/move")
def files_move(body: MoveIn, request: Request):
    with _fs(request):
        return {"count": files.move(body.paths, body.dest, body.as_copy)}


@app.post("/api/files/delete")
def files_delete(body: PathsIn, request: Request):
    with _fs(request):
        return {"trash": files.delete(body.paths)}


@app.get("/api/files/text")
def files_text(path: str, request: Request):
    with _fs(request):
        return files.read_text(path)


@app.put("/api/files/text")
def files_text_save(body: TextIn, request: Request):
    with _fs(request):
        return files.write_text(body.path, body.text, body.mtime)


@app.get("/api/trash")
def trash_get(request: Request):
    with _fs(request):
        return {"items": files.trash_list(), "days": files.TRASH_DAYS}


@app.post("/api/trash/{tid}/restore")
def trash_restore(tid: str, request: Request):
    with _fs(request):
        return {"path": files.trash_restore(tid)}


@app.delete("/api/trash/{tid}")
def trash_remove(tid: str, request: Request):
    with _fs(request):
        return {"count": files.trash_delete(tid)}


@app.delete("/api/trash")
def trash_empty(request: Request):
    with _fs(request):
        return {"count": files.trash_delete(None)}


# ---------- serverkortet ----------

@app.get("/api/card")
def card_data():
    return card.data()


@app.get("/api/icon/{name}")
def app_icon(name: str):
    from fastapi.responses import Response
    body, ctype = card.icon(name)
    return Response(body, media_type=ctype, headers={
        "Cache-Control": "private, max-age=86400",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})


# ---------- nattliga uppdateringar ----------

class UpdatesIn(BaseModel):
    enabled: bool = True
    mode: str = "auto"
    time: str = "04:30"


class UpdatesRunIn(BaseModel):
    mode: str | None = None


class AutoIn(BaseModel):
    on: bool


@app.get("/api/updates")
def updates_get():
    st = updater.state()
    return {"config": updater.cfg(), "runs": st.get("runs", [])[-10:][::-1],
            "available": st.get("available", {})}


@app.put("/api/updates")
def updates_put(body: UpdatesIn):
    return updater.save_cfg(body.enabled, body.mode, body.time)


@app.post("/api/updates/run")
def updates_run(body: UpdatesRunIn):
    if body.mode not in (None, "auto", "notify"):
        raise HTTPException(400, "Okänt läge.")
    return {"job": updater.run_now(body.mode)}


@app.post("/api/apps/{name}/autoupdate")
def app_autoupdate(name: str, body: AutoIn):
    return updater.set_auto(name, body.on)


# ---------- Home Assistant ----------

class HaIn(BaseModel):
    enabled: bool = True
    url: str = ""
    token: str | None = None


@app.get("/api/homeassistant")
def ha_get():
    return homeassistant.public_cfg()


@app.put("/api/homeassistant")
def ha_put(body: HaIn):
    return homeassistant.save(body.enabled, body.url, body.token)


@app.post("/api/homeassistant/test")
def ha_test():
    return homeassistant.test()


# ---------- veckobrev ----------

class LetterIn(BaseModel):
    enabled: bool = True
    weekday: int = 6
    hour: int = 18


@app.get("/api/letter")
def letter_get():
    return {"config": letter.cfg(), "letters": letter.letters()}


@app.put("/api/letter")
def letter_put(body: LetterIn):
    return letter.save_cfg(body.enabled, body.weekday, body.hour)


@app.post("/api/letter/preview")
def letter_preview():
    return letter.compose()


@app.post("/api/letter/send")
def letter_send():
    return letter.send_now()


# ---------- QR-kod ----------

@app.get("/api/qr")
def qr(text: str):
    import io
    import segno
    if not text or len(text) > 600:
        raise HTTPException(400, "Ogiltig text.")
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", scale=8, border=2, dark="#0d0f1a", light="#ffffff",
                                     xmldecl=False, svgns=True)
    from fastapi.responses import Response
    return Response(buf.getvalue(), media_type="image/svg+xml",
                    headers={"Cache-Control": "private, max-age=3600",
                             "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})


# ---------- flytta servern ----------

class MoveExportIn(BaseModel):
    passphrase: str


class MoveImportIn(BaseModel):
    code: str
    passphrase: str


@app.post("/api/move/export")
def move_export(body: MoveExportIn):
    return {"job": move.export_job(body.passphrase)}


@app.post("/api/move/preview")
def move_preview(body: MoveImportIn):
    return move.preview(body.code, body.passphrase)


@app.post("/api/move/import")
def move_import(body: MoveImportIn):
    return {"job": move.import_job(body.code, body.passphrase)}


# ---------- Nestward ----------

class NestwardIn(BaseModel):
    url: str = ""
    token: str | None = None


@app.get("/api/nestward")
def nestward_get():
    return nestward.public_cfg()


@app.put("/api/nestward")
def nestward_put(body: NestwardIn):
    return nestward.save(body.url, body.token)


@app.get("/api/nestward/summary")
def nestward_summary(force: bool = False):
    return nestward.summary(force)


# ---------- inställningar ----------

class SettingsIn(BaseModel):
    server_name: str | None = None
    tz: str | None = None
    puid: str | None = None
    pgid: str | None = None
    bind_ip: str | None = None


@app.get("/api/settings")
def get_settings():
    return config.public()


@app.put("/api/settings")
def put_settings(body: SettingsIn):
    changes = {k: v.strip() for k, v in body.model_dump().items() if v is not None}
    if "bind_ip" in changes and changes["bind_ip"] and \
            not re.match(r"^[0-9a-fA-F:.]+$", changes["bind_ip"]):
        raise HTTPException(400, "Ogiltig IP-adress.")
    for k in ("puid", "pgid"):
        if k in changes and not changes[k].isdigit():
            raise HTTPException(400, "PUID/PGID ska vara siffror.")
    data = config.public(config.update(**changes))
    proxy.safe_apply()
    return data


# ---------- vad kostar min server? ----------

class CostIn(BaseModel):
    kind: str | None = None
    idle_w: float | None = None
    max_w: float | None = None
    price: float | None = None
    ha_entity: str | None = None
    vps_month: float | None = None


@app.get("/api/cost")
def cost_get():
    return cost.summary()


@app.put("/api/cost")
def cost_put(body: CostIn):
    cost.save(body.model_dump())
    return cost.summary()


# ---------- säkerhetskoll ----------

@app.get("/api/security")
def security_get(fresh: bool = False):
    return security.check() if fresh else security.cached()


# ---------- delningar ----------

class ShareIn(BaseModel):
    path: str
    days: int
    pin: str = ""
    note: str = ""


@app.get("/api/shares")
def shares_get():
    return {"shares": shares.active(), "base": proxy.wizz_url()}


@app.post("/api/shares")
def shares_create(body: ShareIn):
    return shares.create(body.path, body.days, body.pin, body.note, by="du")


@app.delete("/api/shares/{token}")
def shares_stop(token: str):
    shares.stop(token)
    return {"ok": True}


_PUBLIC_HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow",
                   "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
                                              "form-action 'self'; frame-ancestors 'none'"}


def _share_locked(token: str, request: Request) -> bool:
    r = shares.get(token)
    return bool(r and r["pin"] and request.cookies.get(f"wzs_{token[:10]}") != shares.unlock_cookie(token))


async def _form(request: Request) -> dict:
    from urllib.parse import parse_qs
    body = (await request.body())[:4096].decode("utf-8", "replace")
    return {k: v[0] for k, v in parse_qs(body).items()}


@app.get("/s/{token}")
def share_page(token: str, request: Request, p: str = ""):
    if not shares.get(token):
        return HTMLResponse(shares.gone_page(), 404, headers=_PUBLIC_HEADERS)
    if _share_locked(token, request):
        return HTMLResponse(shares.pin_page(token), headers=_PUBLIC_HEADERS)
    try:
        return HTMLResponse(shares.view_page(token, p), headers=_PUBLIC_HEADERS)
    except (FileNotFoundError, ValueError, OSError):
        return HTMLResponse(shares.gone_page(), 404, headers=_PUBLIC_HEADERS)


@app.post("/s/{token}/unlock")
async def share_unlock(token: str, request: Request):
    from fastapi.responses import RedirectResponse
    f = await _form(request)
    ip = request.client.host if request.client else "?"
    try:
        ok = shares.check_pin(token, f.get("pin", ""), ip)
    except ValueError as e:
        return HTMLResponse(shares.pin_page(token, str(e)), 429, headers=_PUBLIC_HEADERS)
    if not ok:
        return HTMLResponse(shares.pin_page(token, "Fel kod."), 401, headers=_PUBLIC_HEADERS)
    resp = RedirectResponse(f"/s/{token}", 303)
    resp.set_cookie(f"wzs_{token[:10]}", shares.unlock_cookie(token), max_age=86400, httponly=True,
                    samesite="lax", secure=request.url.scheme == "https", path=f"/s/{token}")
    return resp


@app.get("/s/{token}/dl")
def share_download(token: str, request: Request, p: str = "", zip: bool = False):
    if not shares.get(token) or _share_locked(token, request):
        return HTMLResponse(shares.gone_page(), 404, headers=_PUBLIC_HEADERS)
    try:
        f = shares.target(token, p)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "Finns inte.")
    shares.count_download(token)
    if f.is_dir():
        return StreamingResponse(files.zip_stream(f), media_type="application/zip",
                                 headers={"Content-Disposition": _disposition("attachment", f.name + ".zip"),
                                          **_PUBLIC_HEADERS})
    if not f.is_file():
        raise HTTPException(404, "Finns inte.")
    return FileResponse(f, media_type="application/octet-stream",
                        headers={"Content-Disposition": _disposition("attachment", f.name), **_PUBLIC_HEADERS})


# ---------- e-post ----------

class MailIn(BaseModel):
    host: str | None = None
    port: int | None = None
    user: str | None = None
    password: str | None = None
    sender: str | None = None
    security: str | None = None


class MailTestIn(BaseModel):
    to: str


@app.get("/api/mail")
def mail_get():
    return mail.public_cfg()


@app.put("/api/mail")
def mail_put(body: MailIn):
    return mail.save(body.model_dump())


@app.post("/api/mail/test")
def mail_test(body: MailTestIn):
    mail.send(body.to.strip(), "Test från WizzOS", "Det fungerar! WizzOS kan skicka e-post.")
    return {"ok": True}


# ---------- om jag försvinner ----------

class Trustee(BaseModel):
    name: str = ""
    email: str = ""


class LegacyIn(BaseModel):
    enabled: bool | None = None
    months: int | None = None
    owner_name: str | None = None
    owner_email: str | None = None
    trustees: list[Trustee] | None = None
    letter: str | None = None
    practical: str | None = None
    folders: list[str] | None = None
    share_days: int | None = None
    base_url: str | None = None
    pin: str | None = None


class LegacyTestIn(BaseModel):
    what: str


@app.get("/api/legacy")
def legacy_get():
    return legacy.public()


@app.put("/api/legacy")
def legacy_put(body: LegacyIn):
    return legacy.save(body.model_dump())


@app.post("/api/legacy/test")
def legacy_test(body: LegacyTestIn):
    return {"to": legacy.send_test(body.what)}


@app.post("/api/legacy/tell")
def legacy_tell():
    return {"sent": legacy.tell_trustees()}


@app.get("/alive/{token}")
def alive_get(token: str):
    return HTMLResponse(legacy.alive_page(token, legacy.token_kind(token)), headers=_PUBLIC_HEADERS)


@app.post("/alive/{token}")
async def alive_post(token: str, request: Request):
    f = await _form(request)
    ip = request.client.host if request.client else "?"
    kind = legacy.token_kind(token)
    try:
        legacy.check_in(token, f.get("pin", ""), ip)
    except ValueError as e:
        return HTMLResponse(legacy.alive_page(token, kind, str(e)), 400, headers=_PUBLIC_HEADERS)
    return HTMLResponse(legacy.alive_page(token, kind, ok=True), headers=_PUBLIC_HEADERS)


# ---------- AI-koppling (MCP) ----------

class AiKeyIn(BaseModel):
    name: str
    level: str
    days: int = 90


@app.get("/api/ai-keys")
def ai_keys_get():
    return {"enabled": mcp.enabled(), "keys": mcp.keys(), "diary": mcp.read_diary(80), "base": proxy.wizz_url(),
            "public": config.load().get("ai_public_url", ""),
            "levels": mcp.LEVELS, "tools": [{"name": t[0], "level": t[1], "desc": t[3]} for t in mcp.TOOLS]}


class OnIn(BaseModel):
    enabled: bool


@app.put("/api/ai-link")
def ai_link_toggle(body: OnIn):
    mcp.set_enabled(body.enabled)
    return {"enabled": mcp.enabled()}


@app.post("/api/ai-keys")
def ai_keys_create(body: AiKeyIn):
    return mcp.create_key(body.name, body.level, body.days)


@app.delete("/api/ai-keys/{kid}")
def ai_keys_revoke(kid: str):
    mcp.revoke(kid)
    return {"ok": True}


def _mcp_origin_ok(request: Request) -> bool:
    # Skydd mot DNS-rebinding: en webbläsare från en annan sajt får inte prata med /mcp.
    origin = request.headers.get("origin")
    if not origin:
        return True
    from urllib.parse import urlparse
    host = (request.headers.get("host") or "").split(":")[0]
    return urlparse(origin).hostname in (host, "localhost", "127.0.0.1")


@app.post("/mcp")
async def mcp_post(request: Request):
    if not _mcp_origin_ok(request):
        return JSONResponse({"error": "origin"}, status_code=403)
    if not mcp.enabled():
        # Avstängd: AI-appen ser servern som otillgänglig och laddar inga verktyg (sparar krediter).
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32000,
                             "message": "AI-kopplingen är avstängd i WizzOS. Slå på den på startsidan."}}, status_code=503)
    who = mcp.check(request.headers.get("authorization", ""))
    if not who:
        rm = f"{_base(request)}/.well-known/oauth-protected-resource"
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "Ogiltig eller avstängd nyckel."}},
                            status_code=401, headers={"WWW-Authenticate": f'Bearer realm="wizzos", resource_metadata="{rm}"',
                                                      "Access-Control-Allow-Origin": "*",
                                                      "Access-Control-Expose-Headers": "WWW-Authenticate"})
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Ogiltig JSON."}}, status_code=400)
    kid, key = who
    if isinstance(body, list):
        out = [r for r in [await asyncio.to_thread(mcp.handle, m, kid, key) for m in body] if r]
        return JSONResponse(out) if out else Response(status_code=202)
    r = await asyncio.to_thread(mcp.handle, body, kid, key)
    return JSONResponse(r) if r else Response(status_code=202)


@app.get("/mcp")
def mcp_get():
    # Ingen strömning från servern – klienter får använda vanliga POST-anrop.
    return Response(status_code=405, headers={"Allow": "POST"})


@app.delete("/mcp")
def mcp_delete():
    return Response(status_code=405, headers={"Allow": "POST"})


# ---------- godkännanden (AI frågar, du svarar) ----------

@app.get("/api/approvals")
def approvals_get():
    return {"pending": approvals.pending(), "recent": approvals.recent(15)}


@app.post("/api/approvals/{aid}/{what}")
def approvals_decide(aid: str, what: str):
    if what not in ("yes", "no"):
        raise HTTPException(400, "yes eller no")
    return approvals.decide(aid, what == "yes", "WizzOS")


class BaseIn(BaseModel):
    base: str


@app.put("/api/phone-base")
def phone_base(body: BaseIn):
    # Adressen mobilen når WizzOS på (används i godkännandeknapparna när WizzOS saknar egen adress).
    b = body.base.strip().rstrip("/")
    if b and not re.match(r"^https?://[\w.\-\[\]:]+$", b):
        raise HTTPException(400, "Ogiltig adress.")
    if b != config.load().get("phone_base"):
        config.update(phone_base=b)
    return {"ok": True}


@app.get("/approve/{token}")
def approve_page(token: str):
    return HTMLResponse(approvals.page(token), headers=_PUBLIC_HEADERS)


@app.post("/approve/{token}/{what}")
def approve_post(token: str, what: str):
    r = approvals.by_token(token)
    if not r or what not in ("yes", "no"):
        return HTMLResponse(approvals.page(token), 404, headers=_PUBLIC_HEADERS)
    msg = ""
    try:
        approvals.decide(r["id"], what == "yes", "mobilen")
    except ValueError as e:
        msg = str(e)
    return HTMLResponse(approvals.page(token, msg), headers=_PUBLIC_HEADERS)


# ---------- inloggning via webben för AI-appar (OAuth 2.1) ----------

_CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
         "Access-Control-Allow-Headers": "Authorization, Content-Type, MCP-Protocol-Version", "Cache-Control": "no-store"}


def _base(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto.split(',')[0].strip()}://{host.split(',')[0].strip()}"


@app.get("/.well-known/oauth-protected-resource")
@app.get("/.well-known/oauth-protected-resource/mcp")
def oauth_pr(request: Request):
    return JSONResponse(oauth.resource_metadata(_base(request)), headers=_CORS)


@app.get("/.well-known/oauth-authorization-server")
@app.get("/.well-known/oauth-authorization-server/mcp")
@app.get("/.well-known/openid-configuration")
def oauth_as(request: Request):
    return JSONResponse(oauth.server_metadata(_base(request)), headers=_CORS)


@app.options("/{rest:path}")
def cors_preflight(rest: str):
    if rest.startswith((".well-known/", "oauth/", "mcp")):
        return Response(status_code=204, headers={**_CORS, "Access-Control-Max-Age": "600"})
    return Response(status_code=404)


@app.post("/oauth/register")
async def oauth_register(request: Request):
    try:
        body = await request.json()
        return JSONResponse(oauth.register(body if isinstance(body, dict) else {}), status_code=201, headers=_CORS)
    except ValueError as e:
        return JSONResponse({"error": "invalid_client_metadata" if str(e) != "invalid_redirect_uri" else "invalid_redirect_uri",
                             "error_description": str(e)}, status_code=400, headers=_CORS)


@app.get("/oauth/authorize")
def oauth_authorize(request: Request):
    q = dict(request.query_params)
    hdr = {**_PUBLIC_HEADERS, "Content-Security-Policy": _PUBLIC_HEADERS["Content-Security-Policy"].replace("form-action 'self'", "form-action 'self' https: http://localhost:* http://127.0.0.1:*")}
    if not mcp.enabled():
        return HTMLResponse(oauth.error_page("AI-kopplingen är avstängd i WizzOS. Slå på den på startsidan och försök igen."), 503, headers=hdr)
    try:
        params, c = oauth.check_request(q)
    except ValueError as e:
        return HTMLResponse(oauth.error_page(str(e)), 400, headers=hdr)
    return HTMLResponse(oauth.consent_page(params, c), headers=hdr)


@app.post("/oauth/authorize")
async def oauth_authorize_post(request: Request):
    from fastapi.responses import RedirectResponse
    f = await _form(request)
    ip = request.client.host if request.client else "?"
    hdr = {**_PUBLIC_HEADERS, "Content-Security-Policy": _PUBLIC_HEADERS["Content-Security-Policy"].replace("form-action 'self'", "form-action 'self' https: http://localhost:* http://127.0.0.1:*")}
    kind, val = oauth.decide(f, ip)
    if kind == "redirect":
        return RedirectResponse(val, 302)
    return HTMLResponse(val, headers=hdr)


@app.post("/oauth/token")
async def oauth_token(request: Request):
    import base64 as _b64
    from urllib.parse import unquote
    ctype = request.headers.get("content-type", "")
    if "json" in ctype:
        try:
            f = {k: str(v) for k, v in (await request.json()).items()}
        except (ValueError, AttributeError):
            f = {}
    else:
        f = await _form(request)
    basic = None
    a = request.headers.get("authorization", "")
    if a.lower().startswith("basic "):
        try:
            u, _, p = _b64.b64decode(a[6:]).decode().partition(":")
            basic = (unquote(u), unquote(p))
        except Exception:  # noqa: BLE001
            basic = None
    try:
        return JSONResponse(await asyncio.to_thread(oauth.token, f, basic), headers=_CORS)
    except oauth.OAuthError as e:
        return JSONResponse({"error": e.error, "error_description": e.desc}, status_code=e.status, headers=_CORS)


# ---------- sök i hela servern ----------

@app.get("/api/find")
def find_everything(q: str = "", web: bool = True):
    return finder.search(q, include_web=web)


@app.post("/api/find/reindex")
def find_reindex():
    import threading
    threading.Thread(target=finder.build_index, daemon=True).start()
    return {"ok": True}


# ---------- dokument (innehållssökning + Paperless) ----------

@app.get("/api/documents")
def documents_get():
    return {"index": docindex.status(), "paperless": paperless.public_cfg(),
            "ai_read_docs": bool(config.load().get("ai_read_docs", False)), "types": sorted(docindex.EXTS)}


class DocsIn(BaseModel):
    ai_read_docs: bool


@app.put("/api/documents")
def documents_put(body: DocsIn):
    config.update(ai_read_docs=bool(body.ai_read_docs))
    mcp.diary("Du", "—", {}, True, "AI får nu läsa dokumentens innehåll." if body.ai_read_docs
              else "AI får inte längre läsa dokumentens innehåll.")
    return documents_get()


@app.post("/api/documents/reindex")
def documents_reindex():
    import threading
    threading.Thread(target=docindex.update_index, daemon=True).start()
    return {"ok": True}


class PaperlessIn(BaseModel):
    url: str
    token: str | None = None


@app.put("/api/paperless")
def paperless_put(body: PaperlessIn):
    return paperless.save(body.url, body.token)


@app.post("/api/paperless/test")
def paperless_test():
    return paperless.test()


# ---------- WizzOS uppdaterar sig själv ----------

class SelfUpdIn(BaseModel):
    auto: bool


@app.get("/api/selfupdate")
def selfupdate_get(force: bool = False):
    if force:
        selfupdate.latest(force=True)
    return selfupdate.status()


@app.put("/api/selfupdate")
def selfupdate_put(body: SelfUpdIn):
    selfupdate.save(body.auto)
    return selfupdate.status()


@app.post("/api/selfupdate/run")
def selfupdate_run():
    return selfupdate.start()


# ---------- paket efter mål ----------

class GoalInstallIn(BaseModel):
    trial_minutes: int = 0


@app.get("/api/goals")
def goals_list():
    return {"goals": goals.listing()}


@app.get("/api/goals/{gid}")
def goals_detail(gid: str):
    return goals.detail(gid)


@app.post("/api/goals/{gid}/install")
def goals_install(gid: str, body: GoalInstallIn):
    return goals.install(gid, body.trial_minutes)


# ---------- gränssnitt ----------

app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})
