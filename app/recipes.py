"""WizzOS egna paket: appar som WizzOS själv har satt ihop och ställt in.

De syns i Appbutiken som "WizzOS-paket" och installeras som vilken app som
helst (prova först, backup, ångra, nattliga uppdateringar), men får färdiga
inställningar: svenska, integritet och säkerhet från start.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from . import config

SEARXNG_COMPOSE = """name: searxng
services:
  searxng:
    image: searxng/searxng:latest
    restart: unless-stopped
    network_mode: bridge
    environment:
      TZ: $TZ
    ports:
      - target: 8080
        published: "8888"
        protocol: tcp
    volumes:
      - type: bind
        source: /DATA/AppData/$AppID/config
        target: /etc/searxng
      - type: bind
        source: /DATA/AppData/$AppID/cache
        target: /var/cache/searxng
    cap_drop:
      - ALL
    cap_add:
      - CHOWN
      - SETGID
      - SETUID
      - DAC_OVERRIDE
    security_opt:
      - no-new-privileges:true
    deploy:
      resources:
        limits:
          memory: 512M
    healthcheck:
      test: ["CMD-SHELL", "wget -q --spider http://127.0.0.1:8080/healthz || exit 1"]
      interval: 60s
      timeout: 5s
      retries: 3
      start_period: 30s
"""

SEARXNG_SETTINGS = """# Inställt av WizzOS. Ändra gärna – WizzOS skriver aldrig över den här filen.
use_default_settings: true

general:
  instance_name: "WizzOS Sök"
  privacypolicy_url: false
  donation_url: false
  contact_url: false
  enable_metrics: false

search:
  safe_search: 1
  autocomplete: "duckduckgo"
  default_lang: "sv-SE"
  formats:
    - html
    - json

server:
  secret_key: "{secret}"
  limiter: false
  public_instance: false
  image_proxy: true
  method: "GET"

ui:
  default_locale: "sv"
  query_in_title: false
  infinite_scroll: true
  center_alignment: true
  default_theme: simple
  theme_args:
    simple_style: auto
  results_on_new_tab: false
"""


def _prepare_searxng(name: str, log) -> None:
    base = config.DATA_ROOT / "AppData" / name
    cfg_dir, cache = base / "config", base / "cache"
    for d in (cfg_dir, cache):
        d.mkdir(parents=True, exist_ok=True)
    f = cfg_dir / "settings.yml"
    if not f.exists():
        f.write_text(SEARXNG_SETTINGS.replace("{secret}", secrets.token_hex(32)), "utf-8")
        log("Färdiga inställningar: svenska, ingen spårning, bildproxy, säker sökning (måttlig).")
    # SearXNG-containern kör som root men utan extra rättigheter (cap_drop ALL),
    # så filerna måste ägas av root för att kunna läsas och skrivas.
    for p in (base, cfg_dir, cache, f):
        try:
            os.chown(p, 0, 0)
        except OSError:
            pass
    try:
        os.chmod(f, 0o640)
    except OSError:
        pass


RECIPES: dict[str, dict] = {
    "wizzos/searxng": {
        "key": "wizzos/searxng", "store": "wizzos", "name": "searxng", "title": "SearXNG – din egen sökmotor",
        "tagline": "Sök i Google, Bing, DuckDuckGo och Wikipedia på en gång – utan spårning och reklam. Färdiginställd på svenska.",
        "description": (
            "SearXNG är en privat sökmotor som du kör själv. Den frågar många sökmotorer samtidigt och visar "
            "svaren på en egen, ren sida. Sökmotorerna ser bara din server – inte dig – så ingen bygger en profil "
            "på vad du söker efter, och det finns ingen reklam.\n\n"
            "WizzOS har ställt in den åt dig: svenska som standard, måttligt barnfilter, bilder visas via din egen "
            "server, inga mätningar och en hemlig nyckel som skapas vid installationen. Gör den till standardsökmotor "
            "i webbläsaren och mobilen – WizzOS visar hur."),
        "tips": "", "icon": "https://cdn.jsdelivr.net/gh/searxng/searxng@master/searx/static/themes/simple/img/favicon.svg",
        "thumbnail": "", "screenshots": [], "category": "Network", "developer": "SearXNG (paket av WizzOS)",
        "version": "latest", "website": "https://docs.searxng.org/", "port_map": "8888", "scheme": "http",
        "index": "/", "architectures": ["amd64", "arm64"], "main": "searxng", "services": 1,
        "compose": SEARXNG_COMPOSE, "prepare": _prepare_searxng, "search_engine": True,
    },
}


def all_apps() -> list[dict]:
    return [{k: v for k, v in r.items() if k != "prepare"} for r in RECIPES.values()]


def find(key: str) -> dict | None:
    r = RECIPES.get(key)
    return {k: v for k, v in r.items() if k != "prepare"} if r else None


def prepare(key: str, name: str, log) -> None:
    r = RECIPES.get(key or "")
    if r and r.get("prepare"):
        r["prepare"](name, log)


def search_app() -> dict | None:
    """Den installerade sökmotorn (SearXNG-paketet), om någon."""
    from . import apps
    hits = [a for a in apps.installed()["apps"] if a.get("store_key") == "wizzos/searxng"]
    hits.sort(key=lambda a: (bool(a.get("trial")), a.get("status") != "running"))
    return hits[0] if hits else None
