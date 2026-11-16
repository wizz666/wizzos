"""Filhanteraren: bläddra, ladda upp/ner, byt namn, flytta och radera i /DATA.

Säkerhet:
- Allt hålls inom /DATA. Symlänkar som pekar utanför följs inte.
- /DATA/WizzOS (inställningar, nycklar, återställningspunkter) syns inte och går
  inte att röra härifrån.
- Inget raderas direkt: allt hamnar i papperskorgen i 30 dagar och kan ångras.
  Även en fil som skrivs över (uppladdning eller redigering) sparas där först.
"""
from __future__ import annotations

import json
import mimetypes
import os
import secrets
import shutil
import stat
import threading
import time
import zipfile
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from . import config

TRASH = config.HOME / "trash"
TRASH_DAYS = 30
TEXT_MAX = 2 * 1024 * 1024
PREVIEW_IMAGES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "image/bmp"}


# ---------- sökvägar ----------

# En familjemedlem ser bara sin egen mapp: då pekar "/" på /DATA/Hem/<namn>.
_scope: ContextVar[Path | None] = ContextVar("wizzos_files_scope", default=None)


@contextmanager
def scoped(root: Path | None):
    tok = _scope.set(root)
    try:
        yield
    finally:
        _scope.reset(tok)


def _data_root() -> Path:
    return config.DATA_ROOT.resolve()


def _root() -> Path:
    return _scope.get() or _data_root()


def _data_rel(p: Path) -> str:
    """Sökväg räknad från /DATA oavsett vem som är inloggad (för papperskorgen)."""
    r = p.relative_to(_data_root()).as_posix()
    return "/" + r if r != "." else "/"


def _blocked(p: Path) -> bool:
    home = config.HOME.resolve()
    return p == home or home in p.parents


def _inside(p: Path) -> bool:
    root = _root()
    return p == root or root in p.parents


def resolve(rel: str) -> Path:
    """Gör om en sökväg från gränssnittet ("/AppData/x") till en riktig sökväg
    och kontrollerar att den ligger inom /DATA (symlänkar följs)."""
    rel = (rel or "").replace("\\", "/").strip()
    p = (_root() / rel.lstrip("/")).resolve()
    if not _inside(p) or _blocked(p):
        raise ValueError("Den sökvägen är inte tillåten.")
    return p


def _check_name(name: str) -> str:
    name = (name or "").strip()
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\0" in name:
        raise ValueError("Ogiltigt namn.")
    if len(name.encode()) > 255:
        raise ValueError("Namnet är för långt.")
    return name


def item(rel: str) -> Path:
    """Själva posten (följer INTE en symlänk i sista ledet), så att man kan
    döpa om eller radera en länk utan att röra det den pekar på."""
    rel = (rel or "").replace("\\", "/").rstrip("/")
    parent_rel, _, name = rel.rpartition("/")
    parent = resolve(parent_rel)
    p = parent / _check_name(name)
    if _blocked(p) or p == _root():
        raise ValueError("Den sökvägen är inte tillåten.")
    if not os.path.lexists(p):
        raise ValueError("Filen eller mappen finns inte längre.")
    return p


def to_rel(p: Path) -> str:
    r = p.relative_to(_root()).as_posix()
    return "/" + r if r != "." else "/"


def _chown(p: Path) -> None:
    if not hasattr(os, "chown"):
        return
    s = config.load()
    try:
        os.chown(p, int(s["puid"]), int(s["pgid"]), follow_symlinks=False)
    except (OSError, ValueError):
        pass


def _app_titles() -> dict:
    """AppData/<namn> → appens titel, så att gränssnittet kan visa vilken app
    en mapp hör till."""
    out = {}
    if config.APPS_DIR.exists():
        for d in config.APPS_DIR.iterdir():
            try:
                meta = json.loads((d / "meta.json").read_text("utf-8"))
                out[d.name] = {"title": meta.get("title", d.name), "icon": meta.get("icon", "")}
            except (OSError, ValueError):
                continue
    return out


# ---------- lista ----------

def listing(rel: str) -> dict:
    p = resolve(rel)
    if not p.is_dir():
        raise ValueError("Det är ingen mapp.")
    apps = _app_titles()
    root = _root()
    entries = []
    try:
        names = os.listdir(p)
    except PermissionError:
        raise ValueError("Ingen behörighet att läsa mappen.")
    for name in names:
        full = p / name
        if _blocked(full):
            continue
        try:
            st = full.lstat()
        except OSError:
            continue
        link = stat.S_ISLNK(st.st_mode)
        is_dir = stat.S_ISDIR(st.st_mode)
        if link:
            try:
                tgt = full.resolve()
                is_dir = tgt.is_dir() and _inside(tgt) and not _blocked(tgt)
            except OSError:
                is_dir = False
        e = {"name": name, "dir": is_dir, "size": 0 if is_dir else st.st_size,
             "mtime": int(st.st_mtime), "link": link}
        if p == root / "AppData" and name in apps:
            e["app"] = apps[name]
        entries.append(e)
    entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))
    du = shutil.disk_usage(p)
    app_here = None
    if p.parent == root / "AppData" and p.name in apps:
        app_here = apps[p.name]
    return {"path": to_rel(p), "entries": entries, "free": du.free, "total": du.total,
            "app": app_here}


# ---------- papperskorg ----------

def _trash_put(p: Path, reason: str = "deleted") -> str:
    """Flytta p till papperskorgen. Returnerar id."""
    tid = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    d = TRASH / tid
    d.mkdir(parents=True)
    os.chmod(d, 0o700)
    size = _size(p)
    info = {"orig": _data_rel(p), "name": p.name, "time": int(time.time()), "size": size,
            "dir": p.is_dir() and not p.is_symlink(), "reason": reason}
    shutil.move(str(p), str(d / "item"))
    (d / "info.json").write_text(json.dumps(info, ensure_ascii=False), "utf-8")
    return tid


def _size(p: Path) -> int:
    if p.is_symlink() or not p.is_dir():
        try:
            return p.lstat().st_size
        except OSError:
            return 0
    total = 0
    for dp, _dn, fn in os.walk(p):
        for f in fn:
            try:
                total += os.lstat(os.path.join(dp, f)).st_size
            except OSError:
                pass
    return total


def _scope_prefix() -> str | None:
    sc = _scope.get()
    return _data_rel(sc) if sc else None


def _in_prefix(orig: str, prefix: str | None) -> bool:
    return prefix is None or orig == prefix or orig.startswith(prefix.rstrip("/") + "/")


def trash_list() -> list[dict]:
    out = []
    if not TRASH.exists():
        return out
    prefix = _scope_prefix()
    for d in sorted(TRASH.iterdir(), reverse=True):
        try:
            info = json.loads((d / "info.json").read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if not _in_prefix(info.get("orig", ""), prefix):
            continue
        if prefix:
            info["orig"] = info["orig"][len(prefix):] or "/"
        info["id"] = d.name
        out.append(info)
    return out


def _trash_dir(tid: str) -> Path:
    if not tid or "/" in tid or "\\" in tid or tid.startswith("."):
        raise ValueError("Ogiltigt id.")
    d = TRASH / tid
    if not (d / "info.json").exists():
        raise ValueError("Finns inte i papperskorgen längre.")
    return d


def trash_restore(tid: str) -> str:
    d = _trash_dir(tid)
    info = json.loads((d / "info.json").read_text("utf-8"))
    if not _in_prefix(info["orig"], _scope_prefix()):
        raise ValueError("Finns inte i papperskorgen längre.")
    parent_rel, _, name = info["orig"].rstrip("/").rpartition("/")
    parent = (_data_root() / parent_rel.lstrip("/")).resolve()
    if not _inside(parent) or _blocked(parent):
        raise ValueError("Den sökvägen är inte tillåten.")
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / _check_name(name)
    if os.path.lexists(target):
        # Det finns redan något med samma namn (t.ex. den nya versionen av en
        # redigerad fil) – lägg undan den så att ångra går att ångra.
        _trash_put(target, "replaced")
    shutil.move(str(d / "item"), str(target))
    shutil.rmtree(d, ignore_errors=True)
    return to_rel(target)


def trash_delete(tid: str | None = None) -> int:
    """Radera för gott: ett id, eller allt om tid är None."""
    if not TRASH.exists():
        return 0
    if tid:
        d = _trash_dir(tid)
        info = json.loads((d / "info.json").read_text("utf-8"))
        if not _in_prefix(info.get("orig", ""), _scope_prefix()):
            raise ValueError("Finns inte i papperskorgen längre.")
        dirs = [d]
    elif _scope.get():
        dirs = [TRASH / i["id"] for i in trash_list()]  # bara personens egna
    else:
        dirs = [d for d in TRASH.iterdir() if d.is_dir()]
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)
    return len(dirs)


def trash_prune() -> None:
    cutoff = time.time() - TRASH_DAYS * 86400
    for info in trash_list():
        if info.get("time", 0) < cutoff:
            shutil.rmtree(TRASH / info["id"], ignore_errors=True)


_cleaner = False


def start_cleaner() -> None:
    global _cleaner
    if _cleaner:
        return
    _cleaner = True

    def loop():
        while True:
            try:
                trash_prune()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(6 * 3600)

    threading.Thread(target=loop, daemon=True, name="trash-cleaner").start()


# ---------- ändringar ----------

def delete(paths: list[str]) -> list[str]:
    return [_trash_put(item(r)) for r in paths]


def mkdir(rel_dir: str, name: str) -> str:
    parent = resolve(rel_dir)
    p = parent / _check_name(name)
    if os.path.lexists(p):
        raise ValueError("Det finns redan något med det namnet.")
    p.mkdir()
    _chown(p)
    return to_rel(p)


def rename(rel: str, new_name: str) -> str:
    p = item(rel)
    target = p.parent / _check_name(new_name)
    if os.path.lexists(target):
        raise ValueError("Det finns redan något med det namnet.")
    p.rename(target)
    return to_rel(target)


def move(paths: list[str], dest_rel: str, copy: bool = False) -> int:
    dest = resolve(dest_rel)
    if not dest.is_dir():
        raise ValueError("Målet är ingen mapp.")
    srcs = [item(r) for r in paths]
    for s in srcs:
        if s.is_dir() and not s.is_symlink() and (dest == s or s in dest.parents):
            raise ValueError(f"Kan inte lägga {s.name} i sig själv.")
        if os.path.lexists(dest / s.name):
            raise ValueError(f"{s.name} finns redan i målmappen.")
    for s in srcs:
        if copy:
            if s.is_dir() and not s.is_symlink():
                shutil.copytree(s, dest / s.name, symlinks=True)
            else:
                shutil.copy2(s, dest / s.name, follow_symlinks=False)
        else:
            shutil.move(str(s), str(dest / s.name))
    return len(srcs)


def read_text(rel: str) -> dict:
    p = resolve(rel)
    if not p.is_file():
        raise ValueError("Det är ingen fil.")
    if p.stat().st_size > TEXT_MAX:
        raise ValueError("Filen är för stor för att redigeras här (max 2 MB).")
    raw = p.read_bytes()
    if b"\0" in raw[:8192]:
        raise ValueError("Det här ser inte ut som en textfil.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return {"path": to_rel(p), "text": text, "mtime": int(p.stat().st_mtime)}


def write_text(rel: str, text: str, mtime: int | None = None) -> dict:
    p = resolve(rel)
    if not p.is_file():
        raise ValueError("Det är ingen fil.")
    if mtime and int(p.stat().st_mtime) != int(mtime):
        raise ValueError("Filen har ändrats av något annat sedan du öppnade den. Ladda om och försök igen.")
    data = text.encode("utf-8")
    if len(data) > TEXT_MAX:
        raise ValueError("Texten är för stor (max 2 MB).")
    st = p.stat()
    # Spara den gamla versionen i papperskorgen så att ändringen kan ångras.
    old = p.with_name(f".{p.name}.wz-old")
    shutil.copy2(p, old)
    tid = _trash_put(old, "edited")
    info_f = TRASH / tid / "info.json"
    info = json.loads(info_f.read_text("utf-8"))
    info.update(orig=_data_rel(p), name=p.name)
    info_f.write_text(json.dumps(info, ensure_ascii=False), "utf-8")
    tmp = p.with_name(f".{p.name}.wz-tmp")
    tmp.write_bytes(data)
    os.chmod(tmp, stat.S_IMODE(st.st_mode))
    try:
        os.chown(tmp, st.st_uid, st.st_gid)
    except (OSError, AttributeError):
        pass
    tmp.replace(p)
    return {"path": to_rel(p), "mtime": int(p.stat().st_mtime), "undo": tid}


class Upload:
    """Tar emot en fil i bitar till en temporär fil och flyttar den på plats
    när allt har kommit fram – en avbruten uppladdning lämnar inget halvt."""

    def __init__(self, rel_dir: str, name: str, overwrite: bool):
        self.dir = resolve(rel_dir)
        if not self.dir.is_dir():
            raise ValueError("Målet är ingen mapp.")
        self.target = self.dir / _check_name(name)
        if os.path.lexists(self.target) and not overwrite:
            raise FileExistsError(name)
        if self.target.is_dir():
            raise ValueError("Det finns en mapp med samma namn.")
        self.tmp = self.dir / f".{name}.wz-upload-{secrets.token_hex(3)}"
        self.f = open(self.tmp, "wb")
        self.size = 0

    def write(self, chunk: bytes) -> None:
        self.f.write(chunk)
        self.size += len(chunk)

    def abort(self) -> None:
        try:
            self.f.close()
        finally:
            self.tmp.unlink(missing_ok=True)

    def finish(self) -> dict:
        self.f.close()
        undo = None
        if os.path.lexists(self.target):
            undo = _trash_put(self.target, "replaced")
        self.tmp.replace(self.target)
        _chown(self.target)
        return {"path": to_rel(self.target), "size": self.size, "undo": undo}


# ---------- nedladdning ----------

def content_type(p: Path) -> str:
    t, _ = mimetypes.guess_type(p.name)
    return t or "application/octet-stream"


def safe_inline_type(p: Path) -> str | None:
    """Typ att visa direkt i webbläsaren, eller None (= ladda ner).
    HTML/SVG visas aldrig som sig själva – de kunde köra skript i WizzOS."""
    t = content_type(p)
    if t in PREVIEW_IMAGES or t == "application/pdf" or t.startswith(("video/", "audio/")):
        return t
    if t.startswith("text/") or t in ("application/json", "application/xml", "image/svg+xml",
                                       "application/javascript", "application/x-sh"):
        return "text/plain; charset=utf-8"
    return None


class _Pipe:
    """Skrivbar 'fil' som zipfile skriver till och som vi tömmer i bitar."""

    def __init__(self):
        self.buf = bytearray()
        self.pos = 0

    def write(self, b) -> int:
        self.buf += b
        self.pos += len(b)
        return len(b)

    def tell(self) -> int:
        return self.pos

    def flush(self) -> None:
        pass

    def take(self) -> bytes:
        out = bytes(self.buf)
        self.buf.clear()
        return out


def zip_stream(p: Path):
    """Strömma en mapp som zip utan att bygga hela filen på disk."""
    pipe = _Pipe()
    with zipfile.ZipFile(pipe, "w", zipfile.ZIP_DEFLATED, compresslevel=5) as z:
        base = p.parent
        for dp, dn, fn in os.walk(p):
            dn[:] = [d for d in dn if not _blocked(Path(dp) / d)]
            rel_dir = Path(dp).relative_to(base).as_posix()
            if not fn and not dn:
                z.writestr(rel_dir + "/", b"")
            for f in fn:
                full = Path(dp) / f
                if full.is_symlink() or not full.is_file():
                    continue
                try:
                    zi = zipfile.ZipInfo.from_file(full, f"{rel_dir}/{f}")
                    zi.compress_type = zipfile.ZIP_DEFLATED
                    with open(full, "rb") as src, z.open(zi, "w", force_zip64=True) as dst:
                        while True:
                            chunk = src.read(1024 * 1024)
                            if not chunk:
                                break
                            dst.write(chunk)
                            if len(pipe.buf) > 1024 * 1024:
                                yield pipe.take()
                except OSError:
                    continue
                yield pipe.take()
    yield pipe.take()
