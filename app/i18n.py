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
    d = _load(lang)
    key = re.sub(r"\s+", " ", text.strip())
    if key in d:
        return d[key]
    for rx, names, out, _ in _pats[lang]:
        m = rx.match(key)
        if m:
            vals = {n: d.get(v, v) for n, v in zip(names, m.groups())}
            return re.sub(r"\{(\w+)\}", lambda x: vals.get(x.group(1), x.group(0)), out)
    return text


def _(text: str, lang: str | None = None, **kw) -> str:
    """_("Hej {namn}", namn="Anna") – översätt mallen först, fyll i sedan."""
    lang = lang or current()
    out = _load(lang).get(text, text) if lang != "sv" else text
    return out.format(**kw) if kw else out
