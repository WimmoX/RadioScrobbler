# RadioScrobbler — Lessons Learned

Problemen die we al eens tegenkwamen en opgelost hebben, zodat we er niet
nog een keer tegenaan lopen. Zie ook `PROGRESS.md` voor de bredere context.

**Les 1 — Spotify rate limit door ongethrottelde Search-calls.**
De allereerste versie zocht alle (~2000) unieke nummers achter elkaar op via
de Spotify Search API zonder pauze. Spotify gaf een 429, en spotipy's interne
retry-logica bleef daarna zonder wachttijd herhalen ("Retry will occur after:
0 s") — dat hamerde de API net zo hard door en resulteerde in een keiharde
lockout van **~24 uur** op de net aangemaakte developer-app. Les: nieuwe
Spotify-apps hebben een lage/strikte quota; grote hoeveelheden losse
Search-calls moeten altijd gethrottled + met nette backoff. De echte
oplossing was niet "trager", maar "minder": de cache in `spotify_matches`
zorgt dat een nummer maar één keer ooit opgezocht hoeft te worden.
→ Zelfde `_call_with_retry`-aanpak nu ook hergebruikt voor andere externe
API's (zie `RECCOBEATS.md`), in plaats van het opnieuw uit te vinden.

**Les 2 — "Live" (nu-spelend) regel gaf elke scrape een nieuwe duplicate.**
De rij voor het nu spelende nummer heeft geen vaste tijd ("Live" i.p.v.
"17:14"), dus we vulden zelf `datetime.now()` in — tot op de microseconde
nauwkeurig, waardoor elke scrape een net iets ander tijdstip gaf en de
PRIMARY KEY-dedup niet werkte. Fix: afronden op de minuut, net als de rest.

**Les 3 — Site-eigen data is niet altijd de makkelijkste bron.**
Reverse-engineeren van Pinguin Radio's eigen Angular-app (Supabase-backend,
Firebase, etc.) kostte tijd en leverde uiteindelijk niks bruikbaars op zonder
een browser die JS uitvoert. Een externe aggregator (OnlineRadioBox) bleek
sneller te vinden én makkelijker te scrapen (gewone HTML/AJAX, geen
authenticatie nodig) — check dat soort bronnen eerst voor je in een SPA gaat
graven. Zelfde patroon kwam terug bij ReccoBeats: de documentatiesite zelf is
ook een client-side React-app (niet leesbaar zonder browser), maar de
*losse tekstpagina's* daarbinnen (Rate Limiting, Request And Response) waren
wél gewoon server-rendered en dus prima te scrapen. En de eigenlijke API
(`api.reccobeats.com`, een ander subdomein dan de docs) gaf zelfs bruikbare
foutmeldingen terug die de verplichte parameters verraadden — soms is
gewoon de API zelf aanroepen sneller dan de documentatie proberen te lezen.

**Les 4 — Jingles/station-IDs filteren is zender-specifiek.**
Pinguin Radio logt eigen jingles als "PINGUIN - TOTH 5" e.d.; die filteren we
met een simpele prefix-check op het artiestveld (`JINGLE_PREFIXES`). Bij KINK
kwamen in de steekproef geen vergelijkbare troep-regels voor, dus daar is
(nog) geen filter nodig. Let op: een te brede filter (bv. op "kink") zou
per ongeluk echte artiesten wegfilteren (bv. "The Kinks") — filter dus zo
specifiek mogelijk.
