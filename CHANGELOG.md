# Ändringar

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
