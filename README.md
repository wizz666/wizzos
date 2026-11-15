<p align="center"><img src="app/static/logo.png" width="120" alt="WizzOS"></p>

<h1 align="center">WizzOS</h1>
<p align="center"><b>Din egen server – enkel, trygg och på svenska.</b><br>
En panel för hemservern eller VPS:en: appbutik, filer, backup som bevisas, och ett skyddsnät under allt du gör.</p>

---

## Installera

På en dator eller VPS med **Debian eller Ubuntu** (amd64 eller arm64, t.ex. en mini-PC eller Raspberry Pi 5):

```bash
curl -fsSL https://raw.githubusercontent.com/wizz666/wizzos/main/install.sh | sudo bash
```

Skriptet installerar Docker om det behövs, frågar om servern står **hemma** eller **på internet** (då nås den bara via Tailscale eller en SSH-tunnel), och skriver ut adressen. Första gången väljer du ett lösenord – sedan guidar WizzOS dig genom namn, larm till mobilen, backup och första appen.

Kör samma rad igen för att uppdatera manuellt. Normalt behövs det inte: **WizzOS uppdaterar sig själv**.

## Vad gör WizzOS annorlunda?

**Skyddsnät under allt**
- **Prova först** – installera en app på prov. Gillar du den inte försvinner den spårlöst: containrar, data och nedladdade program.
- **Ångra** – återställningspunkter före varje uppdatering och ändring. "Nyligen borttagna" i 30 dagar.
- **Bevisad backup** – krypterad nattlig backup (restic) till en annan server, en disk eller molnet, och WizzOS *provar att återställa* den.
- **Nattliga uppdateringar** – av apparna och WizzOS själv. Startar något inte efteråt backas det automatiskt.

**Enkelt**
- **Vad vill du göra?** – välj ett mål ("Familjens fotoalbum", "Lösenord för familjen" …) i stället för en app. WizzOS kollar att servern klarar det och visar hur du kommer igång.
- **Appbutik** – hundratals appar från CasaOS-butikerna (och fler du lägger till).
- **Sök i allt** (Ctrl K) – appar, inställningar, filnamn, innehållet i dokument och webben via din egen sökmotor.
- **Filer** – bläddra, ladda upp, dela med länkar som slutar fungera av sig själva.

**Trygghet**
- **Säkerhetskoll i klartext** – öppna portar, standardlösenord, SSH, väntande omstart … med betyg och vad du ska göra.
- **Om jag försvinner** – blir servern orörd i månader och du inte svarar på påminnelser får de du valt ett brev och länkar till det viktigaste.
- **Larm till mobilen** via ntfy – och ett veckobrev om hur servern mår.

**Smart hemma**
- **AI-koppling (MCP)** – låt Claude, ChatGPT m.fl. sköta servern. Tre nivåer; det som är svårt att ångra kräver ditt *Godkänn* i mobilen. AI-dagbok och strömbrytare.
- **Home Assistant** – serverns läge som sensorer. **Elkostnad per app** i kronor.
- **Flytta servern** till en ny dator med en kod.

## Säkerhet

- WizzOS styr Docker på datorn och ska bara nås av dig: hemma bakom routern, eller via Tailscale/SSH-tunnel på en VPS.
- Inloggning med lösenord (scrypt), signerade cookies, skydd mot gissningsförsök.
- Hemligheter (lösenord, nycklar, tokens) sparas bara på servern och visas aldrig för AI.
- Hittar du ett säkerhetsproblem – öppna ett ärende med så lite detaljer som möjligt och be om kontakt.

## Så hänger det ihop

| Del | Var |
|---|---|
| Webbserver + API (FastAPI) | `app/main.py` |
| Appar (docker compose, CasaOS-format) | `app/apps.py`, `app/store.py` |
| Prova först, ångra, uppdateringar | `app/trial.py`, `app/snapshots.py`, `app/updater.py` |
| Backup (restic) | `app/backup.py` |
| Självuppdatering | `app/selfupdate.py` |
| AI-koppling (MCP + OAuth) | `app/mcp.py`, `app/oauth.py`, `app/approvals.py` |
| Gränssnitt (ingen byggprocess) | `app/static/` |

Data ligger i `/DATA` (samma som CasaOS): `/DATA/AppData/<app>` för apparnas data och `/DATA/WizzOS` för WizzOS egna inställningar.

**Utveckla:** klona repot och kör `sudo ./install.sh --dev` – då byggs WizzOS från källkoden.

---

### In English

WizzOS is a self-hosted home server panel (Swedish UI) with a safety net: try apps before keeping them, undo points before every change, verified encrypted backups, nightly updates that roll back on failure, goal-based app packages, full-text search, an AI link (MCP) with phone approvals, plain-language security checks and a "dead man's switch" letter. Install on Debian/Ubuntu with the command above.

## Licens

AGPL-3.0 – se [LICENSE](LICENSE).
