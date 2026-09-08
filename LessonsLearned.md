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

**Les 5 — Onze eigen 429-retry-logica werd overruled door spotipy's eigen,
onzichtbare retry-mechanisme.** Ook ná Les 1 (met `_call_with_retry`/
`retry.py` netjes op orde) liep een simpele test met 30 nummers alsnog vast:
het script hing muurstil, zonder enige eigen "rate limited, wachten..."-print.
Oorzaak: `spotipy.Spotify()` heeft standaard zélf een ingebouwde retry
(`max_retries=3`, urllib3-niveau) die een 429 al onderschept en de volledige
`Retry-After`-tijd (in dit geval 3166s, bijna een uur) blokkerend uitzit —
vóórdat er ooit een `SpotifyException` bij onze eigen retry-wrapper
terechtkomt. Onze eigen retry-code was dus dode code voor dat scenario.
Fix: `spotipy.Spotify(..., retries=0, status_retries=0)`, zodat elke 429
meteen doorgegeven wordt en wíj de enigen zijn die over backoff/pacing
beslissen. Extra vangnet in `retry.py`: als een API een onredelijk lange
`Retry-After` teruggeeft (zoals hier), stopt het script nu meteen met een
duidelijke melding in plaats van er stilzwijgend op te gaan liggen wachten.
→ Check bij elke externe library die "retries" claimt te doen, of die retry
zich ook echt aan onze eigen pacing/zichtbaarheid houdt — anders lijkt het
script "vast te hangen" zonder enige uitleg waarom.

**Les 6 — Playlist aanmaken gaf een kale 403 Forbidden, los van scope/rate
limit.** Na het oplossen van Les 5 liep `get_or_create_playlist()` alsnog
vast op `sp.user_playlist_create(...)`, met een 403 zonder verdere uitleg
("Forbidden") — geen scope-probleem (scope klopte, getest), geen rate limit.
Oorzaak: Spotify heeft in **februari 2026** het oude
`POST /users/{user_id}/playlists`-endpoint (dat `user_playlist_create()` in
spotipy nog gebruikt, en al als *deprecated* gemarkeerd stond) geblokkeerd
voor Development Mode-apps — sinds de deadline van 9 maart 2026 geeft dat
endpoint altijd 403, voor iedereen. Fix: `sp.current_user_playlist_create()`
gebruiken i.p.v. `sp.user_playlist_create()` — die praat wél met het nieuwe
`POST /me/playlists`. Gevonden via een gerichte websearch op de exacte
foutmelding + "2026", niet door verder te gokken met API-calls.
→ Let op `DeprecationWarning`s van libraries als spotipy; die wijzen vaak
vooruit naar precies dit soort breaking changes bij de onderliggende API.

**Les 7 — `find_track_uri()` vertrouwde blind op Spotify's resultaat #1, wat
een keer een fout nummer opleverde.** "Royal Blood - 10 over 10" (radio-
schrijfwijze) matchte op "Come on Over" i.p.v. het juiste "Ten Over Ten" —
terwijl "Ten Over Ten" wél als eerste resultaat in dezelfde zoekrespons zat.
Spotify's eigen rangschikking van losse (niet-exacte) zoekopdrachten is dus
niet betrouwbaar genoeg om zomaar te vertrouwen. Fix in `sync_playlist.py`:
`find_track_uri()` vraagt nu 5 kandidaten op i.p.v. 1, en `_best_candidate()`
kiest zelf de beste op basis van (a) een artiestcheck die stijlverschillen
verdraagt (bv. "Fischer Z" vs Spotify's "Fischer-Z", via `_normalize_artist`)
en meerdere artiesten in één credit aankan (bv. "Bonobo & Joy Crookes" —
Spotify slaat dat op als twee losse artiesten, `_ARTIST_SEPARATORS` splitst
dat), en (b) titel-gelijkenis (`difflib`) mét normalisatie van cijfers/
woorden ("10" ↔ "ten", via `_normalize_numbers`) — anders scoorde de wél
juiste match ("Ten Over Ten" vs "10 over 10") net onder de drempel. Haalt
niets de drempel (`MIN_TITLE_SIMILARITY`), dan liever "geen match" dan een
gok. Getest tegen alle 30 bestaande matches: 1 echte fout gevonden en
gefixt, 4 onschuldige verschillen (zelfde nummer, andere single/album-
editie), verder geen regressies.
→ Bij matching-logica: test niet alleen het ene geval dat je probeert te
fixen, maar draai de nieuwe logica over *alle* bestaande matches heen om te
zien of je niet per ongeluk iets anders kapotmaakt.

**Vervolg op Les 7 — de strengere matching wees eerst te veel goede matches
af.** Bij het aanvullen van de playlist bleek de nieuwe logica ineens 7 van
de 14 nieuwe kandidaten af te wijzen — te veel om toeval te zijn. Twee extra
bugs gevonden:
1. **Accenten werden gesloopt, niet omgezet.** "Josh Caffe" (radio) vs
   Spotify's "Josh Caffé" faalde, omdat `_normalize_artist` niet-ASCII-tekens
   gewoon wegstripte i.p.v. te transcriberen — "Caffé" werd zo "Caff", niet
   "Caffe". Fix: eerst `unicodedata`-gebaseerde accent-transliteratie
   (`_strip_accents`), dán pas niet-alfanumerieke tekens wegstrippen.
2. **Titel-toevoegingen van Spotify (niet van de radio) verpestten de
   score.** Spotify-titels als "Torch - Original 7" Single Version" of
   "(feat. X)" scoorden laag tegen de kale radio-titel "Torch". Fix:
   `_core_title()` strip zulke toevoegingen (feat./with-credits tussen
   haakjes, " - ..."-suffixen) voordat we vergelijken; `_title_similarity()`
   pakt het beste resultaat over meerdere varianten (ruw, cijfer-genormali-
   seerd, kern-titel).
   - Bijeffect: dit maakte "Marliese" en "Marliese - Reimagined" ineens
     gelijkwaardig, waardoor de heruitvoering per ongeluk boven het origineel
     gekozen kon worden. Opgelost met een tie-breaker in `_best_candidate()`:
     bij gelijke score wint de kandidaat met de hoogste *ruwe* (ongestripte)
     gelijkenis — dus bij twijfel de simpelste/exactste match, niet de eerste
     die toevallig langskomt.

**Vervolg op Les 7 — Spotify's zoekresultaten voor obscure/underground
artiesten zijn soms gewoon niet stabiel tussen twee identieke calls.**
"corto.alto - Noblehill" en "JGrrey - bedbug" matchten de ene keer wél
correct, een paar minuten later leverde exact dezelfde zoekopdracht andere
(of geen) resultaten op — geen bug aan onze kant, gewoon wisselende
serverside-ranking bij Spotify zelf voor dunbezaaide metadata. Niet iets om
tegen te vechten met nog meer logica; gewoon zo'n nummer overslaan en een
ander kandidaat-nummer proberen is prima.
