"""Sök i innehållet i dina dokument.

WizzOS läser texten ur dokument under /DATA (PDF, Word, OpenOffice, Excel,
PowerPoint, text, Markdown, CSV, HTML och e-post) och lägger den i ett
sökindex (SQLite FTS5) i /DATA/WizzOS/search.db. Bara ändrade filer läses om,
var 30:e minut, och lugnt i bakgrunden så att servern inte märker det.

Inskannade PDF:er utan text (bara bilder) går inte att söka i – låt Paperless
göra OCR på dem, och koppla Paperless till WizzOS så söks de också.
"""
from __future__ import annotations

import email
import email.policy
import html
import io
import json
import os
import re
import sqlite3
import threading
import time
import zipfile
from pathlib import Path

from . import config

DB = config.HOME / "search.db"
META = config.HOME / "search-meta.json"
EXTS = {".txt", ".md", ".csv", ".html", ".htm", ".pdf", ".docx", ".odt", ".ods", ".odp", ".xlsx", ".pptx", ".eml", ".rtf"}
MAX_BYTES = 60 * 1024 * 1024
MAX_CHARS = 1_500_000
MAX_PDF_PAGES = 400
SKIP_TOP = {"WizzOS", "AppData", "lost+found"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".cache", ".Trash-1000", "@eaDir", ".thumbnails"}
_lock = threading.Lock()
_state = {"running": False, "last": 0, "secs": 0.0, "files": 0, "added": 0, "removed": 0, "failed": 0, "current": ""}


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, mtime INTEGER, size INTEGER, chars INTEGER, ok INTEGER, note TEXT)")
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(path UNINDEXED, name, body, "
              "tokenize='unicode61 remove_diacritics 2')")
    return c


# ---------- läsa text ----------

def _xml_text(data: bytes, para_tags=(b"</w:p>", b"</text:p>", b"</a:p>", b"</si>", b"</text:h>")) -> str:
    for t in para_tags:
        data = data.replace(t, t + b"\n")
    txt = re.sub(rb"<[^>]+>", b" ", data).decode("utf-8", "replace")
    return html.unescape(re.sub(r"[ \t]+", " ", txt))


def _zip_text(p: Path, members) -> str:
    out = []
    with zipfile.ZipFile(p) as z:
        names = z.namelist()
        for pat in members:
            for n in sorted(x for x in names if re.fullmatch(pat, x)):
                info = z.getinfo(n)
                if info.file_size > 40 * 1024 * 1024:
                    continue
                out.append(_xml_text(z.read(n)))
    return "\n".join(out)


def _pdf_text(p: Path) -> str:
    from pypdf import PdfReader
    r = PdfReader(str(p))
    if r.is_encrypted:
        try:
            r.decrypt("")
        except Exception:  # noqa: BLE001
            return ""
    parts, total = [], 0
    for i, page in enumerate(r.pages):
        if i >= MAX_PDF_PAGES or total > MAX_CHARS:
            break
        try:
            t = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            t = ""
        parts.append(t)
        total += len(t)
    return "\n".join(parts)


def _eml_text(p: Path) -> str:
    msg = email.message_from_bytes(p.read_bytes(), policy=email.policy.default)
    head = "\n".join(f"{k}: {msg.get(k, '')}" for k in ("From", "To", "Subject", "Date"))
    body = msg.get_body(preferencelist=("plain", "html"))
    txt = ""
    if body:
        raw = body.get_payload(decode=True) or b""
        cs = body.get_content_charset()
        for enc in ([cs] if cs else []) + ["utf-8", "cp1252"]:
            try:
                txt = raw.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
    if body and body.get_content_type() == "text/html":
        txt = re.sub(r"<[^>]+>", " ", txt)
    return head + "\n\n" + html.unescape(txt)


def extract(p: Path) -> str:
    ext = p.suffix.lower()
    if ext in (".txt", ".md", ".csv"):
        return p.read_bytes()[:MAX_CHARS * 2].decode("utf-8", "replace")
    if ext in (".html", ".htm"):
        raw = p.read_bytes()[:MAX_CHARS * 2].decode("utf-8", "replace")
        raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
        return html.unescape(re.sub(r"<[^>]+>", " ", raw))
    if ext == ".rtf":
        raw = p.read_bytes()[:MAX_CHARS * 2].decode("latin-1", "replace")
        raw = re.sub(r"\\'([0-9a-f]{2})", lambda m: bytes.fromhex(m.group(1)).decode("cp1252", "replace"), raw)
        return re.sub(r"\\[a-z]+-?\d* ?|[{}]", " ", raw)
    if ext == ".pdf":
        return _pdf_text(p)
    if ext == ".docx":
        return _zip_text(p, [r"word/document\.xml", r"word/(header|footer)\d*\.xml"])
    if ext in (".odt", ".ods", ".odp"):
        return _zip_text(p, [r"content\.xml"])
    if ext == ".xlsx":
        return _zip_text(p, [r"xl/sharedStrings\.xml", r"xl/worksheets/sheet\d+\.xml"])
    if ext == ".pptx":
        return _zip_text(p, [r"ppt/slides/slide\d+\.xml"])
    if ext == ".eml":
        return _eml_text(p)
    return ""


# ---------- bygga index ----------

def _walk():
    root = str(config.DATA_ROOT)
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    if (d == root and e.name in SKIP_TOP) or e.name in SKIP_DIRS or e.name.startswith("."):
                        continue
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False) and os.path.splitext(e.name)[1].lower() in EXTS:
                            st = e.stat(follow_symlinks=False)
                            if 0 < st.st_size <= MAX_BYTES:
                                yield e.path[len(root):], int(st.st_mtime), st.st_size
                    except OSError:
                        continue
        except OSError:
            continue


def update_index() -> dict:
    if not _lock.acquire(blocking=False):
        return status()
    t0 = time.time()
    _state.update(running=True, added=0, removed=0, failed=0)
    try:
        c = _conn()
        known = {r[0]: (r[1], r[2]) for r in c.execute("SELECT path, mtime, size FROM files")}
        seen = set()
        n = 0
        for rel, mtime, size in _walk():
            seen.add(rel)
            n += 1
            if known.get(rel) == (mtime, size):
                continue
            _state["current"] = rel
            full = config.DATA_ROOT / rel.lstrip("/")
            try:
                text = extract(full)[:MAX_CHARS]
                text = re.sub(r"\s+\n", "\n", re.sub(r"[ \t\r\f\v]+", " ", text)).strip()
                ok, note = 1, "" if text else "ingen text (inskannad?)"
            except Exception as e:  # noqa: BLE001
                text, ok, note = "", 0, f"{type(e).__name__}"
                _state["failed"] += 1
            c.execute("DELETE FROM docs WHERE path=?", (rel,))
            if text:
                c.execute("INSERT INTO docs(path, name, body) VALUES (?,?,?)", (rel, os.path.basename(rel), text))
            c.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?)", (rel, mtime, size, len(text), ok, note))
            _state["added"] += 1
            if _state["added"] % 25 == 0:
                c.commit()
            time.sleep(0.02)  # snällt mot servern
        gone = [p for p in known if p not in seen]
        for p in gone:
            c.execute("DELETE FROM docs WHERE path=?", (p,))
            c.execute("DELETE FROM files WHERE path=?", (p,))
        c.commit()
        if _state["added"] or gone:
            c.execute("INSERT INTO docs(docs) VALUES('optimize')")
            c.commit()
        c.close()
        _state.update(files=n, removed=len(gone))
    finally:
        _state.update(running=False, last=int(time.time()), secs=round(time.time() - t0, 1), current="")
        try:  # kom ihåg senaste körningen även efter en omstart
            META.write_text(json.dumps({k: _state[k] for k in ("last", "secs", "files", "added", "removed", "failed")}), "utf-8")
        except OSError:
            pass
        _lock.release()
    return status()


def status() -> dict:
    out = dict(_state)
    if not out["last"]:
        try:
            out.update(json.loads(META.read_text("utf-8")))
        except (OSError, ValueError):
            pass
    try:
        c = _conn()
        out["indexed"] = c.execute("SELECT COUNT(*) FROM files WHERE chars>0").fetchone()[0]
        out["no_text"] = c.execute("SELECT COUNT(*) FROM files WHERE chars=0").fetchone()[0]
        c.close()
    except sqlite3.Error:
        out["indexed"] = out["no_text"] = 0
    try:
        out["db_mb"] = round(DB.stat().st_size / 1e6, 1)
    except OSError:
        out["db_mb"] = 0
    return out


# ---------- söka ----------

def _fts_query(q: str) -> str:
    words = re.findall(r"[\wåäöÅÄÖéü-]{2,}", q)
    return " AND ".join('"' + w.replace('"', "") + '"*' for w in words[:8])


def search(q: str, limit: int = 8) -> list[dict]:
    fq = _fts_query(q)
    if not fq or not DB.exists():
        return []
    try:
        c = _conn()
        rows = c.execute(
            "SELECT path, name, snippet(docs, 2, '\x02', '\x03', ' … ', 14) FROM docs WHERE docs MATCH ? "
            "ORDER BY bm25(docs, 0.0, 4.0, 1.0) LIMIT ?", (fq, limit)).fetchall()
        c.close()
    except sqlite3.Error:
        return []
    return [{"path": p, "name": n, "snippet": s} for p, n, s in rows]


def read(rel: str, max_chars: int = 20000) -> dict:
    """Hela texten i ett indexerat dokument (för AI:n, om du tillåtit det)."""
    from . import files
    p = files.resolve(rel)
    if p.suffix.lower() not in EXTS or not p.is_file():
        raise ValueError("Det är inget dokument som WizzOS kan läsa.")
    text = extract(p)
    return {"path": files.to_rel(p), "chars": len(text), "text": text[:max_chars],
            "truncated": len(text) > max_chars}


_started = False


def start_indexer() -> None:
    global _started
    if _started:
        return
    _started = True

    def loop():
        time.sleep(60)
        while True:
            try:
                update_index()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(30 * 60)

    threading.Thread(target=loop, daemon=True, name="doc-index").start()
