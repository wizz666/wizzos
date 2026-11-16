"""Språk på serversidan.

Gränssnittet översätts i webbläsaren (static/i18n.js). Här finns det som
servern själv behöver: vilket språk den som frågar använder (kakan wz-lang),
serverns eget språk för larm, brev och AI (inställningen "language") och
översättning av svenska texter med samma ordlista som webbläsaren."""
from __future__ import annotations

import json
import re
from contextvars import ContextVar
from pathlib import Path

LANGS = ("sv", "en")
_req_lang: ContextVar[str | None] = ContextVar("wizzos_lang", default=None)
_dicts: dict[str, dict] = {}
_pats: dict[str, list] = {}
DIR = Path(__file__).parent / "static" / "i18n"


def server_lang() -> str:
    """Språket för sådant som inte har någon webbläsare: larm, brev, AI-kopplingen."""
    from . import config
    lang = config.load().get("language") or "sv"
    return lang if lang in LANGS else "sv"


def current() -> str:
    return _req_lang.get() or server_lang()


def set_request_lang(lang: str | None):
    return _req_lang.set(lang if lang in LANGS else None)


def _load(lang: str) -> dict:
    if lang not in _dicts:
        try:
            _dicts[lang] = json.loads((DIR / f"{lang}.json").read_text("utf-8"))
        except (OSError, ValueError):
            _dicts[lang] = {}
        pats = []
        for k, v in _dicts[lang].items():
            names = re.findall(r"\{(\w+)\}", k)
            if not names:
                continue
            src = re.sub(r"\\\{(\w+)\\\}", "(.*?)", re.escape(k))
            pats.append((re.compile(f"^{src}$", re.S), names, v, len(k)))
        _pats[lang] = sorted(pats, key=lambda p: -p[3])
    return _dicts[lang]


def tr(text: str, lang: str | None = None) -> str:
    """Översätt en färdig svensk text (även med inbakade värden via mönster)."""
    lang = lang or current()
    if lang == "sv" or not text:
        return text
    _load(lang)
    if "\n" in text:  # flera rader (t.ex. veckobrevet): rad för rad
        return "\n".join(tr(line, lang) if line.strip() else line for line in text.split("\n"))
    lead = text[:len(text) - len(text.lstrip())]
    tail = text[len(text.rstrip()):]
    out = _smart(re.sub(r"\s+", " ", text.strip()), lang)
    return text if out is None else lead + out + tail


_PREFIX = re.compile(r"^([^\w\s(\"'«]+\s*)(.+)$", re.S)
_SWEDISH = re.compile(r"[a-zåäöA-ZÅÄÖ]{2}")


def _smart(key: str, lang: str, depth: int = 0) -> str | None:
    """Hela texten, annars mönster ("{a} appar"), annars utan inledande symbol (✅, 🛡️ …)."""
    d = _dicts[lang]
    if key in d:
        return d[key]
    if depth > 3:
        return None
    for rx, names, out, _ in _pats[lang]:
        m = rx.match(key)
        if m:
            vals = {}
            for n, v in zip(names, m.groups()):
                t = _smart(v, lang, depth + 1) if v != key and _SWEDISH.search(v) else None
                vals[n] = t if t is not None else v
            return re.sub(r"\{(\w+)\}", lambda x: vals.get(x.group(1), x.group(0)), out)
    m = _PREFIX.match(key)
    if m:
        t = _smart(m.group(2), lang, depth + 1)
        if t is not None:
            return m.group(1) + t
    return None


_TAGS = re.compile(r"(<style\b.*?</style>|<script\b.*?</script>|<[^>]+>)", re.S | re.I)
_ATTR = re.compile(r"""\b(placeholder|title|alt|aria-label)=(["'])(.*?)\2""", re.S)


def tr_html(html: str, lang: str | None = None) -> str:
    """Översätt texten i en färdig HTML-sida (sidor som visas utan JavaScript)."""
    lang = lang or current()
    if lang == "sv" or not html:
        return html
    out = []
    for part in _TAGS.split(html):
        if not part:
            continue
        if part.startswith("<"):
            if not part.lower().startswith(("<style", "<script")):
                part = _ATTR.sub(lambda m: f"{m.group(1)}={m.group(2)}{tr(m.group(3), lang)}{m.group(2)}", part)
            out.append(part)
        elif _SWEDISH.search(part):
            out.append(tr(part, lang))
        else:
            out.append(part)
    res = "".join(out)
    return re.sub(r"<html lang=[\"']?sv[\"']?", f'<html lang="{lang}"', res, count=1)


def _(text: str, lang: str | None = None, **kw) -> str:
    """_("Hej {namn}", namn="Anna") – översätt mallen först, fyll i sedan."""
    lang = lang or current()
    out = _load(lang).get(text, text) if lang != "sv" else text
    return out.format(**kw) if kw else out
