"""Hur säkra är mina filer? – viktiga mappar, fotokoll och brandövning.

* Viktiga mappar (t.ex. /DATA/Foton) följer med i den nattliga backupen, med egen tagg.
* Fotokollen räknar kopior per mapp: servern själv + backupen, och om någon kopia
  ligger utanför huset (SFTP/S3/R2). Grönt först när det finns en kopia utanför huset
  som dessutom är provåterställd.
* Brandövningen (en gång i månaden, eller på knapptryck) återställer ALLT på prov:
  varje app startas i en sandlåda och stickprov av filerna i varje mapp hämtas ur
  backupen och jämförs med originalen. Resultatet blir en enkel mening:
  "Om allt går sönder är du igång igen på ungefär X minuter."
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import time
from pathlib import Path

from . import backup, config

DRILL_DAYS = 30
SAMPLE = 20
OUTSIDE = ("sftp", "s3")


# ---------- viktiga mappar ----------

def folders() -> list[dict]:
    return list(backup.cfg().get("folders") or [])


def _check_path(path: str) -> str:
    p = os.path.realpath(path.strip().rstrip("/") or "/")
    if not p.startswith("/DATA/") or p.count("/") < 2:
        raise ValueError("Välj en mapp under /DATA, t.ex. /DATA/Foton.")
    for bad in ("/DATA/WizzOS", "/DATA/AppData", "/DATA/Backup"):
        if p == bad or p.startswith(bad + "/"):
            raise ValueError(f"{bad} sköts redan av WizzOS – välj en egen mapp.")
    if not os.path.isdir(p):
        raise ValueError("Mappen finns inte.")
    return p


def add_folder(path: str, label: str = "") -> list[dict]:
    p = _check_path(path)
    cur = [f for f in folders() if f["path"] != p]
    cur.append({"path": p, "label": (label or os.path.basename(p))[:60]})
    backup.save_cfg(folders=cur)
    return cur


def remove_folder(path: str) -> list[dict]:
    cur = [f for f in folders() if f["path"] != path]
    backup.save_cfg(folders=cur)
    return cur


def _folder_state(path: str, **ch) -> None:
    backup._update_state(lambda s: s.setdefault("folders", {}).setdefault(path, {}).update(ch))


def backup_folder(path: str, log) -> dict:
    tag = f"folder:{path}"
    try:
        backup._cap_check(os.path.basename(path), [path], log, tag=tag)
    except backup.CapExceeded as e:
        _folder_state(path, last_backup=time.time(), backup_ok=False, backup_msg=str(e))
        raise
    log(f"{path}: säkerhetskopierar ...")
    try:
        out = backup.restic(["backup", "--json", "--host", "wizzos", "--tag", "wizzos", "--tag", tag, path], log=log)
    except Exception as e:
        _folder_state(path, last_backup=time.time(), backup_ok=False, backup_msg=str(e)[:300])
        raise
    summ = {}
    for line in out.splitlines():
        if '"message_type":"summary"' in line:
            try:
                summ = json.loads(line)
            except ValueError:
                pass
    info = {"last_backup": time.time(), "backup_ok": True, "backup_msg": "",
            "size": summ.get("total_bytes_processed", 0), "files": summ.get("total_files_processed", 0),
            "snapshot": summ.get("snapshot_id", "")[:8]}
    _folder_state(path, **info)
    log(f"{path}: klar – {backup._human(info['size'])}, {info['files']} filer.")
    return info


# ---------- fotokoll ----------

def _verdict(copies: int, outside: bool, verified: bool) -> tuple[str, str]:
    if copies < 2:
        return "bad", "Finns bara på ett ställe – går servern sönder är filerna borta."
    if not outside:
        return "warn", "Två kopior, men båda i huset – en brand eller ett inbrott tar båda."
    if not verified:
        return "warn", "Kopia utanför huset finns, men den är inte provåterställd än."
    return "ok", "Kopia utanför huset, provåterställd – det här är tryggt."


def report() -> dict:
    c = backup.cfg()
    st = backup.state()
    outside = c.get("type") in OUTSIDE
    drill = st.get("drill") or {}
    fresh = lambda t: bool(t) and time.time() - t < 48 * 3600  # noqa: E731
    rows = []
    for f in folders():
        fs = (st.get("folders") or {}).get(f["path"], {})
        dr = next((x for x in drill.get("folders", []) if x["path"] == f["path"]), {})
        ok_backup = fs.get("backup_ok") and fresh(fs.get("last_backup"))
        verified = bool(dr.get("ok")) and time.time() - (drill.get("time") or 0) < 45 * 86400
        copies = 1 + (1 if ok_backup else 0)
        level, why = _verdict(copies, outside and ok_backup, verified)
        if fs.get("backup_msg"):
            why = fs["backup_msg"]
        rows.append({"path": f["path"], "label": f["label"], "size": fs.get("size"), "files": fs.get("files"),
                     "last_backup": fs.get("last_backup"), "copies": copies,
                     "outside": bool(outside and ok_backup), "verified": verified, "level": level, "why": why})
    apps_st = st.get("apps", {})
    app_ok = bool(apps_st) and all(v.get("backup_ok") and fresh(v.get("last_backup")) for v in apps_st.values())
    lvl, why = _verdict(2 if app_ok else 1, outside and app_ok, bool(drill.get("apps_ok")) and
                        time.time() - (drill.get("time") or 0) < 45 * 86400)
    rows.insert(0, {"path": "", "label": "Appar och deras data", "copies": 2 if app_ok else 1,
                    "outside": bool(outside and app_ok), "level": lvl, "why": why})
    mine = {f["path"] for f in folders()}
    try:
        suggest = sorted(str(d) for d in Path("/DATA").iterdir() if d.is_dir() and str(d) not in mine
                         and d.name not in ("WizzOS", "AppData", "Backup", "lost+found") and not d.name.startswith("."))
    except OSError:
        suggest = []
    return {"target": backup.describe_target(c) if c.get("type") else "", "outside_target": outside,
            "rows": rows, "drill": drill or None, "drill_due": drill_due(), "suggest": suggest[:20]}


# ---------- brandövning ----------

def drill_due() -> bool:
    c = backup.cfg()
    if not (c.get("enabled") and c.get("password") and c.get("type")) or c.get("drill_off"):
        return False
    return time.time() - ((backup.state().get("drill") or {}).get("time") or 0) > DRILL_DAYS * 86400


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _drill_folder(path: str, log) -> dict:
    snaps = json.loads(backup.restic(["snapshots", "--json", "--tag", f"folder:{path}", "--latest", "1"]) or "[]")
    if not snaps:
        return {"path": path, "ok": False, "msg": "Ingen backup av mappen än."}
    snap = snaps[-1]
    nodes = []
    for line in backup.restic(["ls", "--json", snap["short_id"]]).splitlines():
        try:
            n = json.loads(line)
        except ValueError:
            continue
        if n.get("type") == "file" and n.get("path", "").startswith(path + "/"):
            nodes.append(n)
    if not nodes:
        return {"path": path, "ok": False, "msg": "Backupen av mappen är tom."}
    pick = random.sample(nodes, min(SAMPLE, len(nodes)))
    root = backup.TMP_ROOT / f"drill-{int(time.time())}"
    t0 = time.time()
    try:
        args = ["restore", snap["short_id"], "--target", str(root)]
        for n in pick:
            args += ["--include", n["path"]]
        backup.restic(args)
        secs = max(time.time() - t0, 0.5)
        got, same, changed, nbytes = 0, 0, 0, 0
        for n in pick:
            r = root / n["path"].lstrip("/")
            if not r.is_file() or r.stat().st_size != n.get("size", -1):
                continue
            got += 1
            nbytes += r.stat().st_size
            live = Path(n["path"])
            if live.is_file() and live.stat().st_size == n.get("size"):
                if _sha(live) == _sha(r):
                    same += 1
                else:
                    changed += 1  # filen har ändrats efter backupen – inget fel
            else:
                changed += 1
        total = sum(n.get("size", 0) for n in nodes)
        speed = nbytes / secs if nbytes else 0
        est = int(total / speed) if speed else None
        ok = got == len(pick)
        msg = (f"{got} av {len(pick)} stickprov hämtade ur backupen" +
               (f", {same} identiska med originalen" if same else "") + ".")
        log(f"{path}: {'✓' if ok else '✗'} {msg}")
        return {"path": path, "ok": ok, "msg": msg, "sampled": len(pick), "restored": got,
                "identical": same, "files": len(nodes), "size": total, "est_secs": est}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def drill(log) -> dict:
    """Återställ allt på prov. Ändrar ingenting i originalen."""
    from . import alerts
    start = time.time()
    log("🧯 Brandövning: återställer allt på prov – originalen rörs inte.")
    names = [a["name"] for a in backup.apps_in_backup() if a["installed"]]
    app_rows = []
    for n in names:
        t0 = time.time()
        try:
            r = backup._with_app_lock(n, lambda: backup.restore_test(n, log))
            app_rows.append({"name": n, "ok": bool(r.get("ok")), "secs": int(time.time() - t0), "msg": r.get("msg", "")})
        except Exception as e:  # noqa: BLE001
            app_rows.append({"name": n, "ok": False, "secs": int(time.time() - t0), "msg": str(e)[:200]})
    folder_rows = []
    for f in folders():
        try:
            folder_rows.append(_drill_folder(f["path"], log))
        except Exception as e:  # noqa: BLE001
            folder_rows.append({"path": f["path"], "ok": False, "msg": str(e)[:200]})
    apps_ok = all(a["ok"] for a in app_rows)
    ok = apps_ok and all(f["ok"] for f in folder_rows)
    est = sum(a["secs"] for a in app_rows) + sum(f.get("est_secs") or 0 for f in folder_rows)
    res = {"time": start, "duration": int(time.time() - start), "ok": ok, "apps_ok": apps_ok,
           "apps": app_rows, "folders": folder_rows, "estimate_secs": est}
    backup._update_state(lambda s: s.update(drill=res))
    mins = max(1, round(est / 60))
    if ok:
        msg = f"Allt kunde återställas. Går servern sönder är du igång igen på ungefär {mins} minuter."
        log("✓ " + msg)
        alerts.resolve("drill", msg)
        alerts.notify("🧯 Brandövningen gick bra", msg, 2)
    else:
        bad = [a["name"] for a in app_rows if not a["ok"]] + [f["path"] for f in folder_rows if not f["ok"]]
        msg = f"Det här gick inte att återställa på prov: {', '.join(bad)}."
        log("✗ " + msg)
        alerts.raise_alert("drill", "Brandövningen hittade problem", msg, 4)
    return res


def run_drill_now() -> str:
    from . import jobs
    return jobs.start("Brandövning", drill)
