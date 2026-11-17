# Ändringar

## 2.7.0

- **📁 Mina mappar** (Filer → Mina mappar) – skapa en egen mapp med några klick: välj namn, om den ska vara **nåbar från datorn och telefonen** (en vanlig nätverksmapp, t.ex. `\\server\Kvitton`), om den ska vara **med i backupen** och **vem som får se den** (bara du eller hela familjen). Varje person har ett eget nätverkslösenord.
- **📤 Dela till WizzOS** – lägg WizzOS på telefonens startskärm, så finns den i mobilens dela-meny. Foton, kvitton, PDF:er och länkar hamnar direkt i valfri mapp på servern – utan någon app.
- **📥 Smart inkorg** (Filer → Inkorg) – släpp filer i Inkorg så sorteras de efter en minut: foton och filmer till Foton/År/Månad, PDF:er till Paperless (eller Dokument/År), dokument till Dokument/År, musik till Media/Musik. Enkla regler, ingen AI, och varje flytt kan ångras.
- **🗺️ Ditt hem-IT** (Hem → Hela hemmet) – en karta över alla dina servrar och datorer via Nestward: ledigt minne och disk, hur många tjänster som är uppe, vilka som inte hörs av, och var nästa tunga app passar bäst. Kräver Nestward 0.11.
- *English: My folders (network share, backup and who may see it in one step), Share to WizzOS from the phone's share menu, a smart inbox that sorts files by simple rules, and a map of your home IT via Nestward.*

## 2.6.0

- **Flytta WizzOS – hela servern med allt** (Backup → 📦 Flytta till ny server):
  - **🩺 Hälsokoll först** – den nya servern kollar innan något görs om den klarar allt: minne (uppmätt på den gamla servern, sovande appar räknas in), disk, processortyp per app, upptagna portar, enheter och grafikkort. Grönt/gult/rött per app.
  - **Direktflytt i samma nätverk** – den nya servern hämtar allt rakt från den gamla över nätverket (snabbt, utan molnet). Öppet i 6 timmar, bara för datorer i ditt eget nät med nyckeln i flyttkoden.
  - **Allt följer med** – appar, viktiga mappar, familjens mappar och konton, AI-kopplingar, agenten, delningar, Home Assistant och övriga inställningar.
  - **Kontroll efteråt** – varje app måste starta och antalet filer i varje mapp jämförs med den gamla servern. Den gamla servern rörs inte.
- *English: Move WizzOS – a health check of the new server first (memory, disk, CPU type, ports, devices), direct transfer on the same network, everything comes along, and a check afterwards.*

## 2.5.0

- **Sovande appar** (appens meny → 💤 Låt appen sova när den inte används) – en app som ingen använt på 30 min, 1 h eller 3 h stoppas och frigör minnet. Öppnar någon appens adress visas ”💤 vaknar …” och appen startar av sig själv; sidan laddas om när den är klar. Utan egen adress väcks appen när du öppnar den från WizzOS. Appar som jobbar i bakgrunden somnar aldrig. Sovande appar ger inga larm och hoppas över av agenten och nattuppdateringen. Av som standard.
- **🛟 Hur säkra är mina filer?** (Backup-sidan) – lägg till viktiga mappar, t.ex. dina foton, så följer de med i nattens backup. Ett enkelt kort visar för varje mapp hur många kopior som finns, om någon ligger utanför huset och om den är provåterställd: 🔴 bara ett ställe, 🟡 bara i huset eller inte provad, 🟢 tryggt.
- **🧯 Brandövning** – en gång i månaden (eller på knapptryck) återställer WizzOS allt på prov: varje app startas i en sandlåda och stickprov av filerna hämtas ur backupen och jämförs med originalen. Du får veta: ”Går servern sönder är du igång igen på ungefär X minuter.” Originalen rörs aldrig.
- **Flytta in från Unraid** (Appbutik → 📦 Flytta in från Unraid) – välj Unraids appmallar (my-….xml från utdelningen flash, eller en zip) så gör WizzOS om dem till WizzOS-appar med samma image, portar och inställningar. Unraids mappar byts mot WizzOS egna, lösenord följer med men visas aldrig, och det som inte går att göra om automatiskt (grafikkort, egen IP, docker.sock …) listas tydligt. Varje app visar också exakt hur du flyttar dess data.
- *English: How safe are my files? (important folders in the backup, copies/outside-the-house/test-restored per folder) and a monthly fire drill that test-restores everything.*
- *English: Move in from Unraid – turn Unraid's app templates into WizzOS apps, with secrets hidden and a clear list of what to check.*
- *English: Sleeping apps – an unused app is stopped to free memory and wakes up by itself when someone opens it.*

## 2.4.1

- **Säkrare bakom Cloudflare-tunnel** (för dig som når WizzOS utifrån utan Tailscale, t.ex. med Cloudflare Access framför):
  - spärren mot många felaktiga lösenord räknar på besökarens riktiga adress – en angripare kan inte längre låsa ute dig
  - inloggningskakan märks alltid ”bara https” bakom tunneln
  - inloggning via tunneln gäller i 12 timmar och glöms när webbläsaren stängs – bra på en lånad dator
- *English: Safer behind a Cloudflare tunnel – real visitor IP for the login lockout, https-only cookie, and 12-hour sessions that end when the browser closes.*

## 2.4.0

- **WizzOS-agenten** (Inställningar → WizzOS-agenten) – en lätt agent: regler först, AI sist. Den vaknar bara när något går fel (en app stannar, backupen misslyckas, disken fylls, certifikatet förnyas inte) och följer ett fast arbetsflöde:
  - startar om appen en gång, kör backupen igen eller läser in proxyn på nytt – löser det problemet behövs ingen AI alls
  - annars förklarar AI:n vad som troligen är fel, utifrån loggen och dina anteckningar
  - en app som nyss uppdaterats och mår dåligt? Agenten frågar i mobilen om den ska återställas till läget före uppdateringen
  - full disk? Den räknar ut vad som tar plats och frågar om den får rensa programrester som ingen app använder
- Tak för antal AI-anrop per dag, anteckningar som agenten tar hänsyn till och en logg över vad den gjort. Av som standard.
- *English: The WizzOS agent – rules first, AI last. Wakes up only when something goes wrong, fixes the simple things itself and asks on your phone before anything hard to undo.*

## 2.3.0

- **WizzOS-paket** – ett öppet format (`x-wizzos` i en vanlig compose-fil) för appar som kommer färdiginställda: hemliga nycklar, mappar, inställningsfiler, "Så kommer du igång", koppling till WizzOS och backup utan stopp. Se [docs/wizzos-paket.md](docs/wizzos-paket.md).
- **Paperless – papper och kvitton** som nytt paket och mål: svensk och engelsk textigenkänning, en inkorg under Filer (Dokument/Inkorg), admin-konto med starkt lösenord och automatisk koppling till sökningen (Ctrl K).
- **Mer på engelska**: veckobrevet, mejlen i "Om jag försvinner", delningssidor, godkännandesidan, incheckningen och AI-kopplingens verktyg följer serverns språk. Sökningen förstår engelska ord.
- *English: Open WizzOS package format, Paperless as a ready-made package, and the rest of WizzOS in English.*

## 2.2.0

- **WizzOS på engelska** – välj Svenska eller English på inloggningen, under Inställningar → Språk eller under Mitt konto. Valet gäller webbläsaren.
- Ägarens språkval blir också serverns språk: larm till mobilen, AI-hjälpen och AI-kopplingen svarar på det språket.
- Appbutiken visar appbeskrivningarna på engelska när du valt engelska.
- *English: WizzOS now has an English interface. Pick English on the login screen or under Settings → Language.*

## 2.1.0

- **Familjekonton**: ge familj och vänner egna lösenord under Inställningar → Familj och gäster.
  - **Familj** ser bara apparna du väljer och har en egen mapp (Hem/namn) med egen papperskorg.
  - **Gäst** ser bara apparna du väljer.
  - **Admin** får göra allt, precis som du.
- Inloggningen frågar "Vem är du?" så fort det finns fler än du.
- Familj och gäster kan byta sitt eget lösenord under Mitt konto. Byter du deras lösenord eller tar bort dem loggas de ut direkt.
- Bara ägaren räknas som "närvarande" för Om jag försvinner.

## 2.0.0

Första publika versionen av WizzOS.

- **Uppdaterar sig själv** – på natten, efter apparna. Startar den nya versionen inte som den ska backar den automatiskt till den gamla.
- **Välkomstguide** – namn, larm till mobilen (en egen hemlig ntfy-kanal med ett klick), backup och första målet.
- **Vad vill du göra?** – välj ett mål (fotoalbum, lösenord, filmer, eget moln, anteckningar, sökmotor) i stället för en app. WizzOS kollar att servern klarar det och visar hur du kommer igång.
- **Sök i hela servern** (Ctrl K) – appar, inställningar, filnamn, innehållet i dokument (PDF, Word, Excel …), Paperless och webben via din egen sökmotor.
- **Egen sökmotor** – SearXNG färdiginställd på svenska, utan spårning.
- **AI-koppling (MCP)** – låt Claude eller ChatGPT sköta servern med tre nivåer, godkännande i mobilen och en AI-dagbok. Strömbrytare på startsidan.
- **Trygghet** – säkerhetskoll i klartext, delningslänkar som går ut av sig själva och "Om jag försvinner".
- **Elkostnad per app**, **Home Assistant-sensorer**, **Nestward-koppling**, **flytta servern med en kod**, **veckobrev**, **prova först**, **bevisad backup**, **nattliga uppdateringar med ångra**.
