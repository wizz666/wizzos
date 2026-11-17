"""Flytta WizzOS: allt till en ny server – med hälsokoll före och kontroll efter.

Gamla servern: gör en färsk backup och skapar en *flyttkod* – inställningar,
backupens nycklar, familjekonton, AI-kopplingar m.m. plus en *profil* av servern
(vilka appar, hur mycket minne/disk de använder, portar, enheter). Allt krypteras
med ett flyttlösenord du väljer (scrypt + AES-GCM). Koden är värdelös utan lösenordet.

Direktflytt: står servrarna i samma nätverk kan den gamla öppnas för direktflytt
i några timmar. Då hämtar den nya allt rakt därifrån (snabbt, inga molnkostnader)
i stället för via backupen. Bara datorer i det egna nätet (privata/Tailscale-adresser)
med den hemliga nyckeln i flyttkoden släpps in.

Nya servern: klistra in koden → hälsokoll (klarar den här servern allt?) → flytt →
kontroll (startar varje app, stämmer antalet filer?).

Det som hör till själva maskinen följer inte med: inloggningslösenordet,
vem som når apparna (bind_ip), proxyns IP och fjärradressen.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

import yaml
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import apps, backup, config, jobs, proxy

PREFIX = "WZMOVE1."
# Inställningar som hör till maskinen – allt annat följer med.
MACHINE_KEYS = {"password_hash", "secret", "bind_ip", "proxy_enabled", "proxy_ip", "proxy_status",
                "ai_public_url", "wake_key", "backup", "welcome_pending", "phone_base", "move_direct"}
# Filer i /DATA/WizzOS som följer med.
CARRY_FILES = ("users.json", "ai-keys.json", "oauth.json", "shares.json", "legacy.json", "agent/anteckningar.md")
FAMILY_ROOT = "/DATA/Hem"
DIRECT_HOURS = 6


# ---------- kryptering ----------

def _key(passphrase: str, salt: bytes) -> bytes:
    return hashlib.scrypt(passphrase.encode(), salt=salt, n=2 ** 15, r=8, p=1,
                          maxmem=64 * 1024 * 1024, dklen=32)


def _check_pass(passphrase: str) -> str:
    p = (passphrase or "").strip()
    if len(p) < 10:
        raise ValueError("Flyttlösenordet behöver minst 10 tecken.")
    return p


# ---------- profil av den gamla servern ----------

def _size(s: str) -> float:
    m = re.match(r"\s*([\d.]+)\s*([a-zA-Z]+)", s or "")
    units = {"b": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9, "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3}
    return float(m.group(1)) * units.get(m.group(2).lower(), 1) if m else 0.0


def _compose_facts(name: str) -> dict:
    out = {"ports": [], "devices": [], "gpu": False, "host_net": False, "images": []}
    try:
        doc = yaml.safe_load((apps.app_dir(name) / "docker-compose.yml").read_text("utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return out
    for svc in (doc.get("services") or {}).values():
        if not isinstance(svc, dict):
            continue
        if svc.get("image"):
            out["images"].append(str(svc["image"]))
        if svc.get("network_mode") == "host":
            out["host_net"] = True
        for p in svc.get("ports") or []:
            host = p.get("published") if isinstance(p, dict) else str(p).split(":")[-2] if str(p).count(":") >= 1 else None
            if host and str(host).split("-")[0].isdigit():
                out["ports"].append(int(str(host).split("-")[0]))
        for d in svc.get("devices") or []:
            out["devices"].append(str(d).split(":")[0])
        res = ((svc.get("deploy") or {}).get("resources") or {}).get("reservations") or {}
        if res.get("devices") or svc.get("runtime") == "nvidia":
            out["gpu"] = True
    return out


def _dir_size(path: str) -> tuple[int, int]:
    total, count = 0, 0
    for r, _d, fs in os.walk(path):
        for f in fs:
            try:
                total += os.lstat(os.path.join(r, f)).st_size
                count += 1
            except OSError:
                pass
    return total, count


def profile() -> dict:
    from . import system
    mem: dict[str, float] = {}
    try:
        proj = {}
        for line in apps.docker("ps", "--format", '{{.Names}}\t{{.Label "com.docker.compose.project"}}').splitlines():
            n, _, p = line.partition("\t")
            if p:
                proj[n] = p
        for line in apps.docker("stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}", timeout=40).splitlines():
            n, _, m = line.partition("\t")
            if n in proj:
                mem[proj[n]] = mem.get(proj[n], 0) + _size(m.split("/")[0]) / 1e6
    except Exception:  # noqa: BLE001
        pass
    bst = backup.state().get("apps", {})
    out_apps = []
    for a in apps.installed()["apps"]:
        if a.get("trial"):
            continue
        n = a["name"]
        f = _compose_facts(n)
        data_bytes = bst.get(n, {}).get("size") or 0
        if not data_bytes:
            data_bytes = sum(_dir_size(str(p))[0] for p in (apps.app_dir(n), Path(config.DATA_ROOT) / "AppData" / n) if p.exists())
        out_apps.append({"name": n, "title": a.get("title", n), "status": a.get("status"),
                         "asleep": bool(a.get("asleep")), "mem_mb": round(mem.get(n) or a.get("sleep_mem_mb") or 0),
                         "data_bytes": int(data_bytes), **f})
    folders = []
    paths = [f["path"] for f in backup.cfg().get("folders") or []]
    if os.path.isdir(FAMILY_ROOT) and FAMILY_ROOT not in paths:
        paths.append(FAMILY_ROOT)
    for p in paths:
        if os.path.isdir(p):
            size, count = _dir_size(p)
            folders.append({"path": p, "size": size, "files": count})
    st = system.stats()
    return {"arch": apps.arch(), "mem_total": st["mem"]["total"], "apps": out_apps, "folders": folders}


# ---------- flyttkoden ----------

def make_code(passphrase: str, direct: dict | None = None) -> str:
    p = _check_pass(passphrase)
    s = config.load()
    bk = backup.cfg()
    if not (bk.get("type") and bk.get("password")) and not direct:
        raise ValueError("Ställ in backup först – eller öppna för direktflytt i samma nätverk.")
    from . import system
    files = {}
    for rel in CARRY_FILES:
        f = config.HOME / rel
        if f.exists() and f.stat().st_size < 5_000_000:
            files[rel] = f.read_text("utf-8")
    bundle = {"v": 2, "made": int(time.time()), "from": s.get("server_name") or system.hostname(),
              "settings": {k: v for k, v in s.items() if k not in MACHINE_KEYS},
              "proxy_was_on": bool(s.get("proxy_enabled")),
              "backup": {k: v for k, v in bk.items() if k not in ("configured_at",)} if bk.get("type") else None,
              "files": files, "profile": profile(), "direct": direct}
    keyfile = config.HOME / "backup" / "id_ed25519"
    if bk.get("type") == "sftp" and keyfile.exists():
        bundle["sftp_key"] = keyfile.read_text("utf-8")
    raw = json.dumps(bundle, ensure_ascii=False).encode()
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    ct = AESGCM(_key(p, salt)).encrypt(nonce, raw, PREFIX.encode())
    return PREFIX + base64.urlsafe_b64encode(salt + nonce + ct).decode().rstrip("=")


def read_code(code: str, passphrase: str) -> dict:
    code = "".join((code or "").split())
    if not code.startswith(PREFIX):
        raise ValueError("Det där ser inte ut som en flyttkod (den börjar med WZMOVE1.).")
    try:
        blob = base64.urlsafe_b64decode(code[len(PREFIX):] + "=" * (-len(code[len(PREFIX):]) % 4))
        salt, nonce, ct = blob[:16], blob[16:28], blob[28:]
        raw = AESGCM(_key(_check_pass(passphrase), salt)).decrypt(nonce, ct, PREFIX.encode())
    except ValueError:
        raise
    except Exception:  # noqa: BLE001
        raise ValueError("Fel flyttlösenord, eller så är koden inte hel.")
    return json.loads(raw)


# ---------- direktflytt: den gamla servern ----------

def _lan_url() -> str:
    from . import system
    ip = config.load().get("bind_ip") or ""
    if not ip or ip == "0.0.0.0":
        try:
            ip = proxy.suggested_ip() or ""
        except Exception:  # noqa: BLE001
            ip = ""
    if not ip:
        ip = system.hostname()
    return f"http://{ip}:{os.environ.get('WIZZOS_PORT', '8899')}"


def open_direct(url: str = "") -> dict:
    token = secrets.token_urlsafe(32)
    until = int(time.time()) + DIRECT_HOURS * 3600
    config.update(move_direct={"hash": hashlib.sha256(token.encode()).hexdigest(), "until": until})
    return {"url": (url or _lan_url()).rstrip("/"), "token": token, "until": until}


def close_direct() -> None:
    config.update(move_direct=None)


def direct_ok(token: str) -> bool:
    d = config.load().get("move_direct") or {}
    if not d or time.time() > d.get("until", 0):
        return False
    return secrets.compare_digest(hashlib.sha256((token or "").encode()).hexdigest(), d.get("hash", ""))


def stream_app(name: str):
    """tar med appens inställningar + data. Appen pausas medan den läses och startas sedan igen."""
    d = apps.app_dir(name)
    if not d.exists():
        raise ValueError("Appen finns inte.")
    try:
        backup._write_image_manifest(name)
    except Exception:  # noqa: BLE001
        pass
    paths = [str(d).lstrip("/")]
    data = Path(config.DATA_ROOT) / "AppData" / name
    if data.exists():
        paths.append(str(data).lstrip("/"))
    from . import snapshots
    running = snapshots.is_running(name)
    if running:
        subprocess.run(apps._compose(name, "stop"), capture_output=True)
    proc = subprocess.Popen(["tar", "-cf", "-", "-C", "/", *paths], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def gen():
        try:
            while chunk := proc.stdout.read(1 << 20):
                yield chunk
        finally:
            proc.kill()
            proc.wait()
            if running:
                subprocess.run(apps._compose(name, "start"), capture_output=True)
    return gen()


def stream_folder(path: str):
    allowed = {f["path"] for f in backup.cfg().get("folders") or []} | {FAMILY_ROOT}
    real = os.path.realpath(path)
    if real not in allowed or not os.path.isdir(real):
        raise ValueError("Den mappen ingår inte i flytten.")
    proc = subprocess.Popen(["tar", "-cf", "-", "-C", "/", real.lstrip("/")], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def gen():
        try:
            while chunk := proc.stdout.read(1 << 20):
                yield chunk
        finally:
            proc.kill()
            proc.wait()
    return gen()


# ---------- hälsokoll på den nya servern ----------

def _ping_direct(d: dict) -> tuple[bool, str]:
    import httpx
    try:
        r = httpx.get(f"{d['url']}/move/d/{d['token']}/ping", timeout=8)
        if r.status_code == 200:
            return True, r.json().get("from", "")
        return False, "Den gamla servern svarade inte rätt – är direktflytten fortfarande öppen?"
    except Exception:  # noqa: BLE001
        return False, f"Når inte den gamla servern på {d['url']} – är de i samma nätverk?"


def _image_platforms(ref: str) -> set[str] | None:
    try:
        out = apps.docker("manifest", "inspect", ref, timeout=40)
        doc = json.loads(out)
    except Exception:  # noqa: BLE001
        return None
    if doc.get("manifests"):
        return {m.get("platform", {}).get("architecture", "") for m in doc["manifests"]}
    return None  # ett enda format – vet inte vilket utan att hämta


def preflight(b: dict) -> dict:
    """Klarar den här servern allt som ska flyttas hit? Inget ändras."""
    from . import system
    prof = b.get("profile") or {}
    checks: list[dict] = []
    add = lambda lvl, title, msg: checks.append({"level": lvl, "title": title, "msg": msg})  # noqa: E731
    ok_docker, ver = apps.docker_ok()
    add("ok" if ok_docker else "bad", "Docker", f"Version {ver}" if ok_docker else "Docker svarar inte på den här servern.")
    here_arch = apps.arch()
    app_rows = []
    installed = {a["name"] for a in apps.installed()["apps"]}
    used = apps.used_ports()
    for a in prof.get("apps", []):
        lvl, notes = "ok", []
        if a["name"] in installed:
            lvl, notes = "warn", ["Finns redan här – hoppas över."]
        if prof.get("arch") and prof["arch"] != here_arch:
            for img in a.get("images", []):
                plats = _image_platforms(img)
                if plats is not None and here_arch not in plats:
                    lvl = "bad"
                    notes.append(f"{img} finns inte för den här processorn ({here_arch}).")
                elif plats is None:
                    lvl = "warn" if lvl == "ok" else lvl
                    notes.append(f"Osäkert om {img} fungerar på {here_arch}.")
        clash = [p for p in a.get("ports", []) if p in used and a["name"] not in installed]
        if clash:
            lvl = "warn" if lvl == "ok" else lvl
            notes.append(f"Port {', '.join(map(str, clash))} används redan här – appen får inte starta förrän den är ledig.")
        missing = [d for d in a.get("devices", []) if not os.path.exists("/host" + d)]
        if missing:
            lvl = "warn" if lvl == "ok" else lvl
            notes.append(f"Saknar enheten {', '.join(missing)} här.")
        if a.get("gpu") and not (os.path.exists("/host/dev/nvidia0") or os.path.exists("/host/dev/dri")):
            lvl = "warn" if lvl == "ok" else lvl
            notes.append("Använder grafikkort – inget hittat här.")
        app_rows.append({"name": a["name"], "title": a.get("title", a["name"]), "level": lvl, "notes": notes,
                         "mem_mb": a.get("mem_mb", 0), "data_bytes": a.get("data_bytes", 0), "asleep": a.get("asleep")})
    # Minne
    st = system.stats()
    need_mb = sum(a["mem_mb"] for a in app_rows if not a.get("asleep")) * 1.2 + 600
    have_mb = st["mem"]["total"] / 1e6
    lvl = "ok" if have_mb >= need_mb else "warn" if have_mb >= need_mb * 0.75 else "bad"
    add(lvl, "Minne", f"Apparna använder ungefär {need_mb / 1000:.1f} GB (med marginal), den här servern har {have_mb / 1000:.1f} GB."
        + ("" if lvl == "ok" else " Sovande appar kan hjälpa – eller färre appar igång samtidigt."))
    # Disk
    need = (sum(a["data_bytes"] for a in app_rows) + sum(f["size"] for f in prof.get("folders", []))) * 1.1
    free = shutil.disk_usage(config.DATA_ROOT).free
    lvl = "ok" if free >= need * 1.2 else "warn" if free >= need else "bad"
    add(lvl, "Disk", f"Behövs ungefär {backup._human(need)}, ledigt här {backup._human(free)}.")
    if prof.get("arch") and prof["arch"] != here_arch:
        add("warn", "Processor", f"Gamla servern var {prof['arch']}, den här är {here_arch} – varje app kollas nedan.")
    # Källa
    d = b.get("direct")
    if d:
        good, info = _ping_direct(d)
        add("ok" if good else ("warn" if b.get("backup") else "bad"), "Direktflytt",
            f"Når den gamla servern direkt – allt hämtas därifrån." if good else info + (" Backupen används i stället." if b.get("backup") else ""))
    elif b.get("backup"):
        add("ok", "Källa", f"Allt hämtas från backupen ({backup.describe_target(b['backup'])}).")
    worst = "bad" if any(c["level"] == "bad" for c in checks + app_rows) else \
        "warn" if any(c["level"] == "warn" for c in checks + app_rows) else "ok"
    return {"level": worst, "checks": checks, "apps": app_rows, "folders": prof.get("folders", []),
            "from": b.get("from", ""), "made": b.get("made"), "direct": bool(d),
            "proxy_domain": b["settings"].get("proxy_domain", ""), "proxy_was_on": b.get("proxy_was_on")}


def preview(code: str, passphrase: str) -> dict:
    return preflight(read_code(code, passphrase))


# ---------- flytten ----------

def _fetch_tar(url: str, log, dest: str = "/") -> None:
    """Hämta en tar från den gamla servern och packa upp den säkert under dest."""
    import httpx
    with httpx.stream("GET", url, timeout=httpx.Timeout(30, read=600)) as r:
        if r.status_code != 200:
            raise RuntimeError(f"Den gamla servern svarade {r.status_code}.")

        class _R:
            def __init__(self, it):
                self.it, self.buf, self.n, self.t = it, b"", 0, time.time()

            def read(self, size=-1):
                while size < 0 or len(self.buf) < size:
                    try:
                        chunk = next(self.it)
                    except StopIteration:
                        break
                    self.buf += chunk
                    self.n += len(chunk)
                    if time.time() - self.t > 15:
                        log(f"   … {backup._human(self.n)} hämtat")
                        self.t = time.time()
                out, self.buf = (self.buf, b"") if size < 0 else (self.buf[:size], self.buf[size:])
                return out
        with tarfile.open(fileobj=_R(r.iter_bytes(1 << 20)), mode="r|") as tf:
            tf.extractall(dest, filter="data")


def _apply_settings(b: dict, log) -> None:
    config.update(**b["settings"])
    for rel, text in (b.get("files") or {}).items():
        if rel not in CARRY_FILES:
            continue
        f = config.HOME / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, "utf-8")
    log("Inställningar övertagna: namn, språk, appbutiker, larm, AI-hjälp och AI-kopplingar, agenten, "
        "familjekonton, delningar, Home Assistant, Nestward m.m.")


def import_job(code: str, passphrase: str, use_direct: bool = True) -> str:
    b = read_code(code, passphrase)  # fel lösenord märks direkt, inte i jobbet

    def work(log):
        start = time.time()
        log(f"Flyttar från {b.get('from') or 'den gamla servern'} ...")
        _apply_settings(b, log)
        d = b.get("direct") if use_direct else None
        if d and not _ping_direct(d)[0]:
            log("Når inte den gamla servern direkt – använder backupen i stället.")
            d = None
        if b.get("backup"):
            if b.get("sftp_key"):
                kd = config.HOME / "backup"
                kd.mkdir(parents=True, exist_ok=True)
                kf = kd / "id_ed25519"
                kf.write_text(b["sftp_key"], "utf-8")
                os.chmod(kf, 0o600)
            bk = dict(b["backup"])
            bk["existing_password"] = bk.pop("password")
            backup.setup(bk, log)
        elif not d:
            raise RuntimeError("Varken direktflytt eller backup går att nå – inget att flytta från.")
        prof = b.get("profile") or {}
        names = [a["name"] for a in prof.get("apps", [])] or [a["name"] for a in backup.apps_in_backup()]
        snaps = {a["name"]: a["snapshot"] for a in backup.apps_in_backup()} if b.get("backup") else {}
        installed = {a["name"] for a in apps.installed()["apps"]}
        ok, failed = [], []
        for n in names:
            if n in installed:
                log(f"{n} finns redan här – hoppar över.")
                continue
            log(f"—— {n} ——")
            try:
                if d:
                    tmp = backup.TMP_ROOT / f"move-{n}-{int(time.time())}"
                    tmp.mkdir(parents=True, exist_ok=True)
                    try:
                        log("Hämtar direkt från den gamla servern ...")
                        _fetch_tar(f"{d['url']}/move/d/{d['token']}/app/{n}", log, str(tmp))
                        backup.install_from_tree(n, tmp, log)
                    finally:
                        shutil.rmtree(tmp, ignore_errors=True)
                elif n in snaps:
                    backup.restore_app(n, snaps[n], log)
                else:
                    raise RuntimeError("Finns inte i backupen.")
                good, why = apps.wait_healthy(n, log, stable=10)
                (ok if good else failed).append(n)
                if not good:
                    log(f"{n} är här men mår inte bra: {why}")
            except Exception as e:  # noqa: BLE001
                log(f"{n} kunde inte flyttas: {e}")
                failed.append(n)
        # Viktiga mappar + familjens mappar
        fold_rows = []
        for f in prof.get("folders", []):
            p = f["path"]
            log(f"—— 📁 {p} ({backup._human(f.get('size', 0))}) ——")
            try:
                if d:
                    import urllib.parse
                    _fetch_tar(f"{d['url']}/move/d/{d['token']}/folder?path={urllib.parse.quote(p)}", log, "/")
                else:
                    snap = json.loads(backup.restic(["snapshots", "--json", "--tag", f"folder:{p}", "--latest", "1"]) or "[]")
                    if not snap:
                        raise RuntimeError("Mappen finns inte i backupen.")
                    backup.restic(["restore", snap[-1]["short_id"], "--target", "/"], log=log)
                size, count = _dir_size(p)
                same = count >= f.get("files", 0)
                fold_rows.append({"path": p, "ok": same, "files": count, "expected": f.get("files", 0)})
                log(f"{'✓' if same else '✗'} {p}: {count} av {f.get('files', 0)} filer på plats.")
            except Exception as e:  # noqa: BLE001
                log(f"{p} kunde inte flyttas: {e}")
                fold_rows.append({"path": p, "ok": False, "msg": str(e)[:200]})
        if b["settings"].get("proxy_domain") and b.get("proxy_was_on"):
            log(f"Egen adress: {b['settings']['proxy_domain']} är förifylld. Slå på den under Inställningar → Egen adress "
                f"när du vill att adresserna ska peka hit (förslag på IP: {proxy.suggested_ip() or 'serverns Tailscale-IP'}).")
        bad_f = [r["path"] for r in fold_rows if not r["ok"]]
        log("")
        log(f"✅ Klart på {int((time.time() - start) / 60)} min: {len(ok)} appar igång" +
            (f", {len(fold_rows) - len(bad_f)} mappar på plats" if fold_rows else "") +
            (f". Behöver kollas: {', '.join(failed + bad_f)}" if failed or bad_f else ". Allt stämmer.") )
        if d:
            log("Den gamla servern är orörd och kör som vanligt. Stäng direktflytten där när du är nöjd.")
        return {"ok": ok, "failed": failed, "folders": fold_rows}
    return jobs.start("Flyttar hit servern", work)


def export_job(passphrase: str, direct: bool = False, url: str = "") -> str:
    _check_pass(passphrase)

    def work(log):
        dd = None
        if backup.cfg().get("type"):
            log("Gör en färsk backup av alla appar först ...")
            res = backup.backup_all(log)
            if res.get("failed"):
                log(f"Obs: backupen misslyckades för {', '.join(res['failed'])}. De följer med i det skick de hade vid förra backupen.")
        if direct:
            dd = open_direct(url)
            log(f"Direktflytt öppen i {DIRECT_HOURS} timmar på {dd['url']} – bara för datorer i ditt eget nät.")
        log("Mäter apparna (minne, disk, portar) inför hälsokollen på nya servern ...")
        code = make_code(passphrase, dd)
        log("Flyttkoden är klar. Spara den tillsammans med flyttlösenordet.")
        return {"code": code}
    return jobs.start("Förbereder flytt", work)
