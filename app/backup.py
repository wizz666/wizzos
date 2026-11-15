"""Backup som bevisar att den fungerar.

- restic sparar krypterat och bara det som ändrats sedan sist.
- Mål: en mapp under /DATA (t.ex. en USB-disk), en annan server via SFTP,
  eller molnlagring som talar S3 (Cloudflare R2, Backblaze B2 ...).
- Varje natt: backup av alla appar, gallring av gamla backuper och ett
  återställningstest av den app som testades för längst sedan. Testet
  återställer appen i en avskild sandlåda och kontrollerar att den startar.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path

import yaml

from . import apps, config, jobs, proxy, snapshots

BK_DIR = config.HOME / "backup"
STATE_FILE = BK_DIR / "state.json"
KEY_FILE = BK_DIR / "id_ed25519"
CACHE_DIR = BK_DIR / "cache"
TEST_ROOT = config.HOME / "restore-test"
TMP_ROOT = config.HOME / "restore-tmp"
TEST_PREFIX = "wzt-"
DEFAULTS = {"type": "", "path": "/DATA/Backup", "sftp_user": "", "sftp_host": "",
            "sftp_port": "22", "sftp_path": "wizzos-backup", "s3_endpoint": "", "s3_bucket": "",
            "s3_key": "", "s3_secret": "", "password": "", "time": "03:30", "enabled": False,
            "keep_daily": 7, "keep_weekly": 4, "keep_monthly": 6,
            # Tak i GB för hela backupen (0 = inget tak). R2 är gratis upp till 10 GB.
            "max_gb": 0}
SECRET_KEYS = ("password", "s3_secret")
_state_lock = threading.Lock()


# ---------- inställningar och status ----------

def cfg() -> dict:
    return {**DEFAULTS, **(config.load().get("backup") or {})}


def save_cfg(**changes) -> dict:
    c = cfg()
    c.update(changes)
    config.update(backup=c)
    return c


def public_cfg() -> dict:
    c = cfg()
    out = {k: v for k, v in c.items() if k not in SECRET_KEYS}
    out["has_s3_secret"] = bool(c.get("s3_secret"))
    out["configured"] = bool(c.get("type") and c.get("password"))
    out["target"] = describe_target(c)
    return out


def describe_target(c: dict) -> str:
    t = c.get("type")
    if t == "local":
        return f"Mappen {c['path']}"
    if t == "sftp":
        return f"{c['sftp_user']}@{c['sftp_host']}:{c['sftp_path']}"
    if t == "s3":
        host = c['s3_endpoint'].split('//')[-1].rstrip('/')
        if host.endswith(".r2.cloudflarestorage.com"):
            return f"Cloudflare R2 · {c['s3_bucket']}"
        if "backblazeb2.com" in host:
            return f"Backblaze B2 · {c['s3_bucket']}"
        return f"{host}/{c['s3_bucket']}"
    return ""


def state() -> dict:
    with _state_lock:
        try:
            return json.loads(STATE_FILE.read_text("utf-8"))
        except (OSError, ValueError):
            return {"apps": {}, "last_run": None, "last_nightly": ""}


def _update_state(fn) -> None:
    with _state_lock:
        try:
            s = json.loads(STATE_FILE.read_text("utf-8"))
        except (OSError, ValueError):
            s = {"apps": {}, "last_run": None, "last_nightly": ""}
        fn(s)
        BK_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(STATE_FILE)


def _app_state(name: str, **changes) -> None:
    def fn(s):
        s.setdefault("apps", {}).setdefault(name, {}).update(changes)
    _update_state(fn)


# ---------- restic ----------

def public_key() -> str:
    BK_DIR.mkdir(parents=True, exist_ok=True)
    if not KEY_FILE.exists():
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "wizzos-backup",
                        "-f", str(KEY_FILE)], check=True, capture_output=True)
    return KEY_FILE.with_suffix(".pub").read_text().strip()


def _repo_and_env(c: dict) -> tuple[list[str], dict]:
    env = {**os.environ, "RESTIC_PASSWORD": c["password"], "RESTIC_CACHE_DIR": str(CACHE_DIR)}
    extra: list[str] = []
    t = c["type"]
    if t == "local":
        repo = c["path"]
    elif t == "sftp":
        public_key()
        repo = f"sftp:{c['sftp_user']}@{c['sftp_host']}:{c['sftp_path']}"
        ssh = (f"ssh -i {KEY_FILE} -p {int(c.get('sftp_port') or 22)} "
               f"-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile={BK_DIR / 'known_hosts'} "
               f"-o BatchMode=yes -o ConnectTimeout=20 "
               f"{c['sftp_user']}@{c['sftp_host']} -s sftp")
        extra = ["-o", f"sftp.command={ssh}"]
    elif t == "s3":
        repo = f"s3:{c['s3_endpoint'].rstrip('/')}/{c['s3_bucket']}"
        env["AWS_ACCESS_KEY_ID"] = c["s3_key"]
        env["AWS_SECRET_ACCESS_KEY"] = c["s3_secret"]
        if "r2.cloudflarestorage.com" in c["s3_endpoint"]:
            extra = ["-o", "s3.region=auto"]
    else:
        raise ValueError("Inget backupmål är valt.")
    return ["restic", "-r", repo, *extra], env


def _scrub(text: str, c: dict) -> str:
    for k in SECRET_KEYS:
        if c.get(k):
            text = text.replace(c[k], "•••")
    return text


def restic(args: list[str], c: dict | None = None, log=None, timeout: int | None = None) -> str:
    """Kör restic. Med log strömmas rader (utom JSON-förlopp) till jobbloggen."""
    c = c or cfg()
    base, env = _repo_and_env(c)
    p = subprocess.Popen([*base, *args], env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    out = []
    assert p.stdout
    start = time.time()
    for line in p.stdout:
        out.append(line)
        if log and line.strip() and not line.startswith('{"message_type":"status"'):
            if not line.startswith("{"):
                log(_scrub(line.rstrip(), c))
        if timeout and time.time() - start > timeout:
            p.kill()
            raise RuntimeError("restic tog för lång tid.")
    if p.wait() != 0:
        msg = _scrub("".join(out[-15:]).strip(), c)
        raise RuntimeError(_friendly(msg))
    return "".join(out)


def _friendly(msg: str) -> str:
    low = msg.lower()
    if "wrong password" in low or "no key found" in low:
        return "Fel återställningsnyckel för den här backupen."
    if "permission denied (publickey" in low:
        return "Servern godtog inte WizzOS nyckel. Har du lagt in den publika nyckeln i authorized_keys?"
    if "connection refused" in low or "timed out" in low or "no route" in low:
        return "Kunde inte ansluta till backupmålet."
    if "is there a repository" in low or "does not exist" in low:
        return "Det finns ingen backup på det stället än."
    return msg[-600:] or "restic misslyckades"


# ---------- sätta upp mål ----------

def setup(new: dict, log) -> dict:
    """Spara mål, testa anslutning, skapa förrådet (eller anslut till ett befintligt)."""
    c = cfg()
    t = new.get("type")
    if t not in ("local", "sftp", "s3"):
        raise ValueError("Välj var backupen ska sparas.")
    for k in ("path", "sftp_user", "sftp_host", "sftp_port", "sftp_path", "s3_endpoint",
              "s3_bucket", "s3_key", "time"):
        if k in new and new[k] is not None:
            c[k] = str(new[k]).strip()
    if new.get("s3_secret"):
        c["s3_secret"] = new["s3_secret"].strip()
    c["type"] = t
    if t == "local":
        p = Path(c["path"])
        if not str(p).startswith(str(config.DATA_ROOT) + "/"):
            raise ValueError("Mappen måste ligga under /DATA (t.ex. /DATA/Backup eller en USB-disk monterad där).")
        p.mkdir(parents=True, exist_ok=True)
    elif t == "sftp":
        if not (c["sftp_user"] and c["sftp_host"] and c["sftp_path"]):
            raise ValueError("Fyll i användare, server och mapp.")
    elif t == "s3":
        if not (c["s3_endpoint"].startswith("https://") and c["s3_bucket"] and c["s3_key"] and c["s3_secret"]):
            raise ValueError("Fyll i adress (https://...), bucket, nyckel-id och hemlig nyckel.")
    if not re.match(r"^\d{2}:\d{2}$", c["time"]):
        raise ValueError("Skriv tiden som 03:30.")
    existing_key = (new.get("existing_password") or "").strip()
    created = False
    if existing_key:
        c["password"] = existing_key
        log("Ansluter till en befintlig backup ...")
        restic(["cat", "config"], c)
        log("Nyckeln stämmer – backupen hittades.")
    else:
        c["password"] = c.get("password") or secrets.token_urlsafe(24)
        log("Kontrollerar backupmålet ...")
        try:
            restic(["cat", "config"], c)
            log("Det finns redan en backup här som går att läsa med nyckeln.")
        except RuntimeError as e:
            if "ingen backup" not in str(e).lower():
                raise
            log("Skapar ett nytt krypterat backupförråd ...")
            restic(["init"], c, log)
            created = True
    if new.get("max_gb") not in (None, ""):
        c["max_gb"] = max(0.0, float(new["max_gb"]))
    elif t == "s3" and "r2.cloudflarestorage.com" in c["s3_endpoint"] and not c.get("max_gb"):
        c["max_gb"] = 9  # håll oss under R2:s gratis 10 GB
        log("Tak satt till 9 GB, så att backupen håller sig inom R2:s gratisnivå.")
    c["enabled"] = True
    c["configured_at"] = c.get("configured_at") or time.time()
    config.update(backup=c)
    log("Backupen är inställd. Första körningen sker " + f"i natt kl {c['time']}.")
    return {"created": created}


# ---------- backup ----------

def _write_image_manifest(name: str) -> None:
    """Spara exakt vilken programversion appen kör, så att en återställning
    kan hämta samma version även på en ny server."""
    out = {}
    for svc, img in snapshots.service_images(name).items():
        digests = []
        try:
            digests = json.loads(apps.docker("image", "inspect", "-f", "{{json .RepoDigests}}",
                                             img["id"]).strip() or "[]") or []
        except Exception:  # noqa: BLE001
            pass
        out[svc] = {"ref": img["ref"], "id": img["id"], "digests": digests}
    (apps.app_dir(name) / "images.json").write_text(json.dumps(out, indent=2), "utf-8")


def repo_size() -> int:
    """Hur mycket backupen faktiskt tar på målet (komprimerat)."""
    out = restic(["stats", "--mode", "raw-data", "--json"])
    for line in out.splitlines():
        if line.startswith("{"):
            return int(json.loads(line).get("total_size", 0))
    return 0


class CapExceeded(RuntimeError):
    pass


def _cap_check(name: str, paths: list[str], log) -> None:
    """Stoppa innan taket passeras: provkör backupen (laddar inte upp något)
    och se hur mycket nytt som skulle tillkomma."""
    c = cfg()
    cap = float(c.get("max_gb") or 0) * 1024 ** 3
    if not cap:
        return
    used = repo_size()
    out = restic(["backup", "--dry-run", "--json", "--host", "wizzos", "--tag", "wizzos",
                  "--tag", f"app:{name}", *paths])
    added = 0
    for line in out.splitlines():
        if '"message_type":"summary"' in line:
            s = json.loads(line)
            added = s.get("data_added_packed") or s.get("data_added") or 0
    _update_state(lambda s: s.update(repo_size=used, cap_bytes=cap))
    if used + added > cap:
        raise CapExceeded(
            f"Taket på {c['max_gb']:g} GB skulle passeras (använt {_human(used)}, "
            f"{name} skulle lägga till {_human(added)}). Ingen uppladdning gjordes. "
            "Höj taket under Backup om du vill.")


def backup_app(name: str, log) -> dict:
    d = apps.app_dir(name)
    if not d.exists():
        raise ValueError("Appen finns inte.")
    _write_image_manifest(name)
    paths = [str(d)]
    dd = snapshots.data_dir(name)
    if dd.exists():
        paths.append(str(dd))
    try:
        _cap_check(name, paths, log)
    except CapExceeded as e:
        _app_state(name, last_backup=time.time(), backup_ok=False, backup_msg=str(e))
        raise
    running = snapshots.is_running(name)
    if running:
        log(f"{name}: pausar en kort stund ...")
        subprocess.run(apps._compose(name, "stop"), capture_output=True)
    try:
        out = restic(["backup", "--json", "--host", "wizzos", "--tag", "wizzos",
                      "--tag", f"app:{name}", *paths], log=log)
    except Exception as e:
        _app_state(name, last_backup=time.time(), backup_ok=False, backup_msg=str(e))
        raise
    finally:
        if running:
            subprocess.run(apps._compose(name, "start"), capture_output=True)
    summary = {}
    for line in out.splitlines():
        if '"message_type":"summary"' in line:
            try:
                summary = json.loads(line)
            except ValueError:
                pass
    info = {"last_backup": time.time(), "backup_ok": True, "backup_msg": "",
            "size": summary.get("total_bytes_processed", 0),
            "added": summary.get("data_added", 0), "files": summary.get("total_files_processed", 0),
            "snapshot": summary.get("snapshot_id", "")[:8]}
    _app_state(name, **info)
    log(f"{name}: klar – {_human(info['size'])} totalt, {_human(info['added'])} nytt.")
    return info


def _human(b: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.0f} {u}"
        b /= 1024
    return f"{b:.1f} TB"


def _with_app_lock(name: str, fn):
    with apps._busy_lock:
        if name in apps._busy:
            raise ValueError(f"{name} håller på med något annat just nu.")
        apps._busy.add(name)
    try:
        return fn()
    finally:
        with apps._busy_lock:
            apps._busy.discard(name)


def backup_all(log) -> dict:
    names = [a["name"] for a in apps.installed()["apps"] if not a.get("trial")]
    ok, failed = [], []
    for n in names:
        try:
            _with_app_lock(n, lambda: backup_app(n, log))
            ok.append(n)
        except Exception as e:  # noqa: BLE001
            log(f"{n}: misslyckades – {e}")
            failed.append(n)
    c = cfg()
    log("Gallrar gamla backuper ...")
    try:
        restic(["forget", "--tag", "wizzos", "--group-by", "host,tags",
                "--keep-daily", str(c["keep_daily"]), "--keep-weekly", str(c["keep_weekly"]),
                "--keep-monthly", str(c["keep_monthly"]), "--prune"], log=None)
    except Exception as e:  # noqa: BLE001
        log(f"Gallringen misslyckades: {e}")
    try:
        used = repo_size()
        _update_state(lambda s: s.update(repo_size=used))
        cap = float(c.get("max_gb") or 0)
        log(f"Backupen tar nu {_human(used)}" + (f" av taket {cap:g} GB." if cap else "."))
    except Exception:  # noqa: BLE001
        pass
    return {"ok": ok, "failed": failed}


# ---------- lista ----------

def list_snapshots(name: str | None = None) -> list[dict]:
    args = ["snapshots", "--json", "--tag", f"app:{name}" if name else "wizzos"]
    data = json.loads(restic(args) or "[]")
    out = []
    for s in data:
        app = next((t[4:] for t in s.get("tags", []) if t.startswith("app:")), "")
        out.append({"id": s["short_id"], "time": s["time"], "app": app,
                    "size": (s.get("summary") or {}).get("total_bytes_processed", 0)})
    out.sort(key=lambda s: s["time"], reverse=True)
    return out


def apps_in_backup() -> list[dict]:
    """Appar som finns i backupen – även de som inte är installerade här."""
    installed = {a["name"] for a in apps.installed()["apps"]}
    by_app: dict[str, dict] = {}
    for s in list_snapshots():
        if s["app"] and s["app"] not in by_app:
            by_app[s["app"]] = {"name": s["app"], "latest": s["time"], "snapshot": s["id"],
                                "installed": s["app"] in installed}
    return sorted(by_app.values(), key=lambda a: a["name"])


# ---------- återställa ----------

def _restore_to(snap: str, target: Path, log) -> None:
    if not re.match(r"^[a-f0-9]{8,64}$", snap):
        raise ValueError("Ogiltig backup.")
    target.mkdir(parents=True, exist_ok=True)
    log("Hämtar filerna från backupen ...")
    restic(["restore", snap, "--target", str(target)], log=log)


def _ensure_images(manifest_file: Path, log) -> None:
    """Hämta exakt samma programversion som när backupen togs."""
    if not manifest_file.exists():
        return
    try:
        man = json.loads(manifest_file.read_text("utf-8"))
    except ValueError:
        return
    for svc, img in man.items():
        try:
            apps.docker("image", "inspect", img["id"], timeout=20)
            apps.docker("tag", img["id"], img["ref"])
            continue  # finns redan på den här datorn
        except Exception:  # noqa: BLE001
            pass
        for dig in img.get("digests") or []:
            try:
                log(f"Hämtar {svc} i samma version som i backupen ...")
                apps.docker("pull", dig, timeout=1800)
                apps.docker("tag", dig, img["ref"])
                break
            except Exception:  # noqa: BLE001
                continue


def restore_app(name: str, snap: str, log) -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    tmp = TMP_ROOT / f"{name}-{stamp}"
    try:
        _restore_to(snap, tmp, log)
        src_app = tmp / str(config.APPS_DIR).lstrip("/") / name
        src_data = tmp / str(config.DATA_ROOT).lstrip("/") / "AppData" / name
        if not (src_app / "docker-compose.yml").exists():
            raise RuntimeError("Backupen saknar appens inställningar.")
        d = config.APPS_DIR / name
        if d.exists():
            snapshots.create(name, "restore", log, start_after=False)
            log("Stoppar appen ...")
            subprocess.run(apps._compose(name, "down", "--remove-orphans"), capture_output=True)
        for src, dst in ((src_app, d), (src_data, snapshots.data_dir(name))):
            if not src.exists():
                continue
            old = dst.parent / f".{dst.name}.old-{stamp}"
            if dst.exists():
                dst.rename(old)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            shutil.rmtree(old, ignore_errors=True)
        _ensure_images(d / "images.json", log)
        log("Startar appen ...")
        jobs.run_cmd(apps._compose(name, "up", "-d", "--remove-orphans", "--pull", "missing"), log)
        proxy.safe_apply()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- återställningstest ----------

def _sandbox_compose(text: str, name: str, root: Path) -> dict:
    """Gör appens compose-fil ofarlig: egen data, inga portar, inga fasta
    namn, och andra mappar (t.ex. /DATA/Media) bara läsbara."""
    real = str(snapshots.data_dir(name))
    text = re.sub(re.escape(real) + r"(?=[/'\":\s]|$)", str(root) + real, text)
    doc = yaml.safe_load(text)
    doc["name"] = TEST_PREFIX + name
    for svc in doc["services"].values():
        for k in ("ports", "container_name", "hostname"):
            svc.pop(k, None)
        svc["restart"] = "no"
        if svc.get("network_mode") == "host":
            svc["network_mode"] = "bridge"
        labels = svc.get("labels") or {}
        if isinstance(labels, dict):
            labels.pop("wizzos.app", None)
            labels["wizzos.test"] = name
            svc["labels"] = labels
        vols = []
        for v in svc.get("volumes") or []:
            if isinstance(v, str):
                parts = v.split(":")
                if parts[0].startswith("/") and not parts[0].startswith(str(root)) \
                        and not parts[0].startswith("/var/run/") and len(parts) >= 2:
                    v = ":".join(parts[:2]) + ":ro"
            elif isinstance(v, dict) and v.get("type") == "bind":
                src = str(v.get("source", ""))
                if not src.startswith(str(root)) and not src.startswith("/var/run/"):
                    v = {**v, "read_only": True}
            vols.append(v)
        if "volumes" in svc:
            svc["volumes"] = vols
    return doc


def _pin_sandbox_images(doc: dict, manifest_file: Path, log) -> None:
    """Låt provkopian köra exakt den version som backupen gjordes med –
    via imagens id eller digest, utan att tagga om något som originalappen använder."""
    try:
        man = json.loads(manifest_file.read_text("utf-8"))
    except (OSError, ValueError):
        return
    for svc_name, svc in doc["services"].items():
        img = man.get(svc_name)
        if not img:
            continue
        try:
            apps.docker("image", "inspect", img["id"], timeout=20)
            svc["image"] = img["id"]
            continue
        except Exception:  # noqa: BLE001
            pass
        for dig in img.get("digests") or []:
            try:
                log(f"Hämtar {svc_name} i samma version som i backupen ...")
                apps.docker("pull", dig, timeout=1800)
                svc["image"] = dig
                break
            except Exception:  # noqa: BLE001
                continue


def restore_test(name: str, log) -> dict:
    """Återställ senaste backupen av appen i en sandlåda och se att den startar."""
    snaps = list_snapshots(name)
    if not snaps:
        raise ValueError("Det finns ingen backup av appen än.")
    snap = snaps[0]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    root = TEST_ROOT / f"{name}-{stamp}"
    project = f"{TEST_PREFIX}{name}-{secrets.token_hex(3)}"
    compose_file = root / "test-compose.yml"
    result = {"ok": False, "msg": "", "files": 0, "size": 0, "snapshot": snap["id"]}
    try:
        log(f"Återställningstest av {name} från backupen {snap['id']} ...")
        _restore_to(snap["id"], root, log)
        restored_app = root / str(config.APPS_DIR).lstrip("/") / name
        if not (restored_app / "docker-compose.yml").exists():
            raise RuntimeError("Backupen saknar appens inställningar.")
        count, size = 0, 0
        for r, _d, fs in os.walk(root):
            for f in fs:
                count += 1
                try:
                    size += os.lstat(os.path.join(r, f)).st_size
                except OSError:
                    pass
        result.update(files=count, size=size)
        log(f"{count} filer ({_human(size)}) återställda.")
        doc = _sandbox_compose((restored_app / "docker-compose.yml").read_text("utf-8"), name, root)
        doc["name"] = project
        _pin_sandbox_images(doc, restored_app / "images.json", log)
        compose_file.write_text(yaml.safe_dump(doc, sort_keys=False), "utf-8")
        env_file = restored_app / ".env"
        cmd = ["docker", "compose", "-p", project, "--project-directory", str(restored_app),
               "-f", str(compose_file)]
        if env_file.exists():
            cmd += ["--env-file", str(env_file)]
        log("Startar en provkopia av appen i en sandlåda (utan portar, utan att röra originalet) ...")
        jobs.run_cmd([*cmd, "up", "-d", "--pull", "missing"], log)
        ok, why = apps.wait_healthy(project, log, timeout=150, stable=20)
        result["ok"] = ok
        result["msg"] = "Appen startade med den återställda datan." if ok else why
        log(("✓ " if ok else "✗ ") + result["msg"])
    except Exception as e:  # noqa: BLE001
        result["msg"] = str(e)
        log(f"✗ Testet misslyckades: {e}")
    finally:
        subprocess.run(["docker", "compose", "-p", project, "-f", str(compose_file), "down",
                        "-v", "--remove-orphans"] if compose_file.exists() else ["true"],
                       capture_output=True)
        for _ in range(5):
            shutil.rmtree(root, ignore_errors=True)
            if not root.exists():
                break
            time.sleep(2)
        log("Sandlådan är städad." if not root.exists() else "Obs: sandlådan kunde inte tas bort helt (städas vid nästa start).")
    _app_state(name, last_test=time.time(), test_ok=result["ok"], test_msg=result["msg"],
               test_files=result["files"], test_size=result["size"], test_snapshot=result["snapshot"])
    return result


# ---------- natt-schema ----------

def nightly(log) -> dict:
    start = time.time()
    log("Nattlig backup startar.")
    res = backup_all(log)
    # Testa den app som testades för längst sedan (och som har en backup).
    st = state().get("apps", {})
    candidates = [n for n in res["ok"]]
    tested = None
    if candidates:
        candidates.sort(key=lambda n: (st.get(n, {}).get("last_test") or 0))
        tested = candidates[0]
        try:
            _with_app_lock(tested, lambda: restore_test(tested, log))
        except Exception as e:  # noqa: BLE001
            log(f"Återställningstestet kunde inte köras: {e}")
    # Kontrollera förrådet en gång i veckan (söndagar).
    if time.localtime().tm_wday == 6:
        log("Veckokontroll av backupförrådet ...")
        try:
            restic(["check"], log=None)
            log("Förrådet är helt.")
        except Exception as e:  # noqa: BLE001
            log(f"Kontrollen hittade problem: {e}")
            res["failed"].append("(kontroll)")
    run = {"time": start, "duration": int(time.time() - start), "ok": not res["failed"],
           "apps": len(res["ok"]), "failed": res["failed"], "tested": tested}

    def fn(s):
        s["last_run"] = run
        s["last_nightly"] = time.strftime("%Y-%m-%d")
    _update_state(fn)
    log("Klart." if run["ok"] else f"Klart, men med fel: {', '.join(res['failed'])}")
    # Larm
    from . import alerts
    st = state().get("apps", {})
    if run["ok"]:
        alerts.resolve("backup", f"Nattens backup gick bra ({run['apps']} appar).")
    else:
        why = "; ".join(f"{n}: {st.get(n, {}).get('backup_msg', '')[:160]}" for n in res["failed"]
                        if n in st) or ", ".join(res["failed"])
        alerts.raise_alert("backup", "Backupen misslyckades",
                           f"Det gick inte att säkerhetskopiera: {why}", 4)
    if tested:
        t = st.get(tested, {})
        if t.get("test_ok"):
            alerts.resolve(f"test:{tested}", f"Återställningstestet av {tested} gick bra.")
        elif t.get("last_test"):
            alerts.raise_alert(f"test:{tested}", f"Återställningstest av {tested} misslyckades",
                               f"Backupen av {tested} kunde inte startas i provet: "
                               f"{t.get('test_msg', '')[:200]}", 4)
    return run


def run_backup_now() -> str:
    return jobs.start("Backup av alla appar", nightly)


def cleanup_leftovers() -> None:
    """Ta bort rester efter avbrutna test/återställningar (t.ex. vid omstart)."""
    try:
        rows = apps.docker("ps", "-a", "--filter", "label=wizzos.test",
                           "--format", '{{.Label "com.docker.compose.project"}}').split()
        for proj in set(rows):
            subprocess.run(["docker", "compose", "-p", proj, "down", "-v", "--remove-orphans"],
                           capture_output=True)
            subprocess.run(["docker", "rm", "-f", *apps.docker(
                "ps", "-aq", "--filter", f"label=com.docker.compose.project={proj}").split()],
                capture_output=True)
    except Exception:  # noqa: BLE001
        pass
    for root in (TEST_ROOT, TMP_ROOT):
        shutil.rmtree(root, ignore_errors=True)


_sched = False


def start_scheduler() -> None:
    global _sched
    if _sched:
        return
    _sched = True

    def loop():
        cleanup_leftovers()
        time.sleep(30)
        while True:
            try:
                c = cfg()
                if c.get("enabled") and c.get("password") and c.get("type"):
                    now = time.localtime()
                    hh, mm = (int(x) for x in c["time"].split(":"))
                    minutes_past = (now.tm_hour * 60 + now.tm_min) - (hh * 60 + mm)
                    # Ikappkörning om servern var avstängd vid utsatt tid, men
                    # bara inom 8 timmar – annars väntar vi till nästa natt.
                    due = 0 <= minutes_past <= 8 * 60
                    today = time.strftime("%Y-%m-%d")
                    busy = any(j["title"] == "Backup av alla appar" for j in jobs.running())
                    if due and state().get("last_nightly") != today and not busy:
                        jobs.start("Backup av alla appar", nightly)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(60)
    threading.Thread(target=loop, daemon=True).start()


def summary() -> dict:
    c = public_cfg()
    s = state()
    tests = [v for v in s.get("apps", {}).values() if v.get("last_test")]
    last_test = max(tests, key=lambda v: v["last_test"]) if tests else None
    return {"configured": c["configured"], "enabled": c.get("enabled"), "time": c.get("time"),
            "target": c["target"], "last_run": s.get("last_run"),
            "repo_size": s.get("repo_size"), "max_gb": c.get("max_gb") or 0,
            "last_test": ({"time": last_test["last_test"], "ok": last_test.get("test_ok")}
                          if last_test else None)}
