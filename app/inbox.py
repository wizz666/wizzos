"""Smart inkorg: släpp filer i /DATA/Inkorg så sorterar WizzOS dem – med enkla regler, utan AI.

  foton/filmer  → /DATA/Foton/ÅÅÅÅ/MM   (datum från bilden, filnamnet eller filen)
  PDF           → Paperless om det finns, annars /DATA/Dokument/ÅÅÅÅ
  dokument      → /DATA/Dokument/ÅÅÅÅ
  musik         → /DATA/Media/Musik
  övrigt        → ligger kvar

En fil flyttas först när den legat still en minut (så att uppladdningar hinner bli klara).
Varje flytt loggas och kan ångras.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

from . import apps, config

INBOX = Path(config.DATA_ROOT) / "Inkorg"
LOG = config.HOME / "inbox-log.json"
STABLE_SECS = 60
KINDS = {
    "photo": {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".dng", ".raw", ".cr2", ".nef", ".arw"},
    "video": {".mp4", ".mov", ".m4v", ".3gp", ".mkv", ".avi"},
    "pdf": {".pdf"},
    "doc": {".doc", ".docx", ".odt", ".xls", ".xlsx", ".ods", ".ppt", ".pptx", ".txt", ".rtf", ".csv"},
    "music": {".mp3", ".flac", ".m4a", ".ogg", ".wav", ".opus"},
}
LABEL = {"photo": "Foton", "video": "Foton", "pdf": "Dokument", "doc": "Dokument", "music": "Musik"}
_DATE_EXIF = re.compile(rb"((?:19|20)\d{2}):([01]\d):([0-3]\d) [0-2]\d:[0-5]\d:[0-5]\d")
_DATE_NAME = re.compile(r"((?:19|20)\d{2})[-_.]?([01]\d)[-_.]?([0-3]\d)")
_lock = threading.Lock()


def cfg() -> dict:
    return {"enabled": False, **(config.load().get("inbox") or {})}


def set_enabled(on: bool) -> dict:
    config.update(inbox={**cfg(), "enabled": bool(on)})
    if on:
        _mkdir(INBOX)
    return cfg()


def _mkdir(p: Path) -> None:
    s = config.load()
    created = []
    q = p
    while not q.exists():
        created.append(q)
        q = q.parent
    p.mkdir(parents=True, exist_ok=True)
    for c in created:
        try:
            os.chown(c, int(s["puid"]), int(s["pgid"]))
        except (OSError, KeyError, ValueError):
            pass


def kind_of(name: str) -> str | None:
    ext = os.path.splitext(name)[1].lower()
    return next((k for k, exts in KINDS.items() if ext in exts), None)


def _date(path: Path) -> tuple[str, str]:
    m = _DATE_NAME.search(path.name)
    if m and 1 <= int(m.group(2)) <= 12:
        return m.group(1), m.group(2)
    if kind_of(path.name) in ("photo", "video"):
        try:
            with open(path, "rb") as fh:
                head = fh.read(256 * 1024)
            m = _DATE_EXIF.search(head)
            if m and 1 <= int(m.group(2)) <= 12:
                return m.group(1).decode(), m.group(2).decode()
        except OSError:
            pass
    t = time.localtime(path.stat().st_mtime)
    return str(t.tm_year), f"{t.tm_mon:02d}"


def _paperless_inbox() -> Path | None:
    p = Path(config.DATA_ROOT) / "Dokument" / "Inkorg"
    try:
        if p.is_dir() and any(a.get("store_key") == "wizzos/paperless" and a.get("status") == "running"
                              for a in apps.installed()["apps"]):
            return p
    except Exception:  # noqa: BLE001
        pass
    return None


def destination(path: Path) -> Path | None:
    k = kind_of(path.name)
    root = Path(config.DATA_ROOT)
    if k in ("photo", "video"):
        y, m = _date(path)
        return root / "Foton" / y / m
    if k == "pdf":
        return _paperless_inbox() or root / "Dokument" / _date(path)[0]
    if k == "doc":
        return root / "Dokument" / _date(path)[0]
    if k == "music":
        return root / "Media" / "Musik"
    return None


def _unique(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem, ext = dest.stem, dest.suffix
    for n in range(2, 1000):
        c = dest.with_name(f"{stem} ({n}){ext}")
        if not c.exists():
            return c
    raise RuntimeError("För många filer med samma namn.")


def _log(rows: list[dict]) -> None:
    try:
        old = json.loads(LOG.read_text("utf-8"))
    except (OSError, ValueError):
        old = []
    LOG.write_text(json.dumps((rows + old)[:300], ensure_ascii=False), "utf-8")


def recent() -> list[dict]:
    try:
        return json.loads(LOG.read_text("utf-8"))[:50]
    except (OSError, ValueError):
        return []


def sort_once(force: bool = False) -> list[dict]:
    """Sortera det som ligger still i inkorgen. Returnerar flyttarna."""
    if not INBOX.is_dir():
        return []
    moved = []
    now = time.time()
    with _lock:
        for f in sorted(INBOX.iterdir()):
            if not f.is_file() or f.name.startswith("."):
                continue
            try:
                if not force and now - f.stat().st_mtime < STABLE_SECS:
                    continue
                dest_dir = destination(f)
                if not dest_dir:
                    continue
                _mkdir(dest_dir)
                dest = _unique(dest_dir / f.name)
                shutil.move(str(f), str(dest))
                moved.append({"t": int(now), "name": f.name, "from": str(f), "to": str(dest),
                              "kind": kind_of(f.name)})
            except Exception:  # noqa: BLE001
                continue
    if moved:
        _log(moved)
    return moved


def undo(to: str) -> dict:
    rows = recent()
    row = next((r for r in rows if r["to"] == to), None)
    if not row:
        raise ValueError("Hittar inte den flytten.")
    src = Path(row["to"])
    if not src.is_file():
        raise ValueError("Filen finns inte kvar där den hamnade.")
    _mkdir(INBOX / ".ångrat")
    back = _unique(INBOX / ".ångrat" / src.name)  # i en dold undermapp så den inte sorteras igen direkt
    shutil.move(str(src), str(back))
    try:
        all_rows = json.loads(LOG.read_text("utf-8"))
        LOG.write_text(json.dumps([r for r in all_rows if r["to"] != to], ensure_ascii=False), "utf-8")
    except (OSError, ValueError):
        pass
    return {"back": str(back)}


def summary() -> dict:
    waiting = []
    if INBOX.is_dir():
        waiting = [f.name for f in INBOX.iterdir() if f.is_file() and not f.name.startswith(".")]
    return {**cfg(), "path": str(INBOX), "waiting": len(waiting),
            "unsorted": [n for n in waiting if not kind_of(n)][:20], "recent": recent()[:15],
            "paperless": bool(_paperless_inbox())}


def start_watch() -> None:
    def loop():
        time.sleep(45)
        while True:
            try:
                if cfg().get("enabled"):
                    sort_once()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(60)
    threading.Thread(target=loop, daemon=True, name="inbox").start()
