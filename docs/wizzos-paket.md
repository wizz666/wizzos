# WizzOS-paket

Ett WizzOS-paket är en vanlig `docker-compose.yml` i CasaOS-format (med `x-casaos`)
plus ett extra block, `x-wizzos`. Blocket talar om för WizzOS vad som ska göras utöver
en vanlig installation, så att appen blir färdig att använda direkt.

Paketen kan ligga i vilken appbutik som helst (`Apps/<namn>/docker-compose.yml`).
WizzOS egna paket finns i [`app/paket/Apps`](../app/paket/Apps). Andra appsystem
(CasaOS m.fl.) ignorerar blocket, så samma fil fungerar även där.

*English: A WizzOS package is a normal CasaOS-style compose file with an extra
`x-wizzos` block. All fields are described below; text fields accept `sv_SE`/`en_US`.*

## Exempel

```yaml
x-wizzos:
  version: 1
  secrets:                  # slumpas vid installationen
    SECRET_KEY: 50          # antal tecken (12–128)
    ADMIN_PASSWORD: 16
  dirs:                     # skapas före första start
    - path: data            # relativ = /DATA/AppData/<app>/data
    - path: /DATA/Dokument/Inkorg
    - path: config
      owner: root           # root eller app (standard: PUID/PGID)
  files:                    # skapas en gång – skrivs aldrig över
    - path: config/settings.yml
      owner: root
      mode: "0640"
      content: |
        secret_key: "{{SECRET_KEY}}"
        language: "{{LANG}}"
  env:                      # skrivs till wizzos.env bredvid compose-filen
    APP_SECRET: "{{SECRET_KEY}}"
    APP_URL: "{{APP_URL}}"
  after_install:            # visas i installationsloggen
    - sv_SE: "Logga in med admin / {{ADMIN_PASSWORD}}"
      en_US: "Log in with admin / {{ADMIN_PASSWORD}}"
  backup:
    before:                 # körs i appen före backupen – då stoppas den inte
      service: db
      command: pg_dump -U app app > /backup/app.sql
  link: paperless           # kopplingar WizzOS kan göra själv (just nu: paperless)
  search_engine: false      # true = WizzOS kan använda appen som sökmotor (SearXNG)
  goal:                     # visas under "Vad vill du göra?"
    id: papers
    emoji: "🗂️"
    min_ram_gb: 2
    needs_https: false
    title: {sv_SE: Papper och kvitton, en_US: Papers and receipts}
    pitch: {sv_SE: "…", en_US: "…"}
    disk: {sv_SE: "…", en_US: "…"}
    steps:
      - {sv_SE: "…", en_US: "…"}
```

Tjänsten som ska få `env` behöver raden `env_file: [wizzos.env]` i compose-filen.

## Värden

| Värde | Betyder |
|---|---|
| `{{NAMN}}` för varje nyckel i `secrets` | Slumpad hemlighet, skapas en gång per installation |
| `{{APP_ID}}` | Appens namn på servern |
| `{{PUID}}`, `{{PGID}}`, `{{TZ}}` | Serverns användare, grupp och tidszon |
| `{{LANG}}`, `{{LOCALE}}` | `sv`/`en` och `sv-SE`/`en-GB` |
| `{{APP_URL}}` | Appens https-adress om Egen adress är påslagen, annars tomt |

`{{ … }}` används i stället för `${ … }` så att docker compose inte försöker fylla i dem.
Blocket tas bort ur den compose-fil som docker compose får.

## Säkerhet

- Mappar och filer måste ligga under `/DATA` och aldrig i WizzOS egen mapp.
- Filer skrivs bara om de inte redan finns – dina ändringar skrivs aldrig över.
- `wizzos.env` får rättigheterna 600 och följer med i backupen.
- Hemligheter visas bara i installationsloggen om paketet själv ber om det (`after_install`).
