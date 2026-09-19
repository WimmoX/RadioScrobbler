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

**Vervolg op Les 7 — bandnamen mét "&"/"/" erin werden onterecht als twee
artiesten gesplitst.** Bekende nummers als "Echo & The Bunnymen - The
Killing Moon" en "AC/DC - You Shook Me All Night Long" faalden, want
`_ARTIST_SEPARATORS` splitste "Echo & The Bunnymen" op de "&" (bedoeld voor
featuring-credits als "Bonobo & Joy Crookes") tot "Echo" + "The Bunnymen" —
geen van beide matcht Spotify's ene artiest "Echo & the Bunnymen". Zelfde
verhaal voor "/" bij "AC/DC". Los daarvan faalden ook "Bangles - Walk Like
An Egyptian" en "Stranglers - Golden Brown", omdat Spotify ze credit als
"The Bangles"/"The Stranglers" en een verschil in een lidwoord de artiest-
match liet mislukken. Twee fixes in `sync_playlist.py`:
1. `_artist_matches()` checkt nu zowel de losse delen (voor échte collabs)
   als de hele, ongesplitste naam (voor bandnamen met "&"/"/" erin) — wat
   matcht met Spotify, telt.
2. `_normalize_artist()` strip een leidend "The " voor de vergelijking.
Getest tegen alle 80 bestaande matches: 12 eerdere "geen match"-gevallen nu
wél gevonden, geen enkele regressie (ook de eerder gefixte collab-gevallen
als "Bonobo & Joy Crookes" bleven goed werken).
→ Bij "splits op scheidingsteken"-logica: vergeet niet dat het scheidings-
teken soms gewoon *onderdeel* is van de ene, ongesplitste naam. Check beide
interpretaties in plaats van te moeten kiezen welke van tevoren.

**Les 8 — `PRAGMA table_info` verbergt generated/virtual kolommen, wat een
migratie-check in een oneindige crash-loop had kunnen laten lopen.** Bij het
toevoegen van `daynr`/`hr`/`daypart` als *generated columns* (afgeleid van
`played_at`, zie hierboven) bleek SQLite geen `STORED` generated column toe
te staan via `ALTER TABLE` op een bestaande tabel (alleen bij het aanmaken
van een nieuwe tabel) — opgelost door `VIRTUAL` te gebruiken (berekend bij
het lezen i.p.v. opgeslagen, verwaarloosbaar traag op onze schaal), wat wél
via `ALTER TABLE` mag. Grotere valkuil: de idempotentie-check in
`_migrate()` (die checkt of een kolom al bestaat voor die 'm toevoegt)
gebruikte `PRAGMA table_info(plays)` — en die geeft generated/virtual
kolommen simpelweg niet terug, ook al bestaan ze wél en werken ze prima in
query's. Daardoor dacht de check bij elke volgende `connect()`-aanroep dat
de kolommen nog ontbraken, en probeerde ze opnieuw toe te voegen —
`sqlite3.OperationalError: duplicate column name`, bij *elk* script dat de
database opent. Fix: `PRAGMA table_xinfo(plays)` gebruiken, die toont
generated/hidden kolommen wél. Dit werd alleen gevonden omdat ik expliciet
`connect()` een tweede keer testte na de migratie — de eerste keer werkte
feilloos, dus zonder die tweede test was dit pas bij de volgende scriptrun
(door de gebruiker, niet door mij) aan het licht gekomen.
→ Test een migratie altijd door de verbindingscode minstens twee keer
achter elkaar te draaien, niet maar één keer — "het werkte de eerste keer"
zegt niets over idempotentie.

**Les 9 — `GET /v1/tracks` (batch, meerdere ID's tegelijk) geeft 403 voor
Development Mode-apps; `GET /v1/tracks/{id}` (enkelvoud) werkt gewoon.**
Bij het normaliseren van `spotify_matches` naar aparte `artists`/`tracks`/
`track_artists`/`track_match`-tabellen (zie hierboven) moest voor 107 al
gematchte nummers de échte, gestructureerde artiestenlijst van Spotify
opgehaald worden (i.p.v. onze eigen zoek-tekst hergebruiken). De voor de
hand liggende, zuinige aanpak — `sp.tracks([...])`, batches van 50 — gaf
een kale 403 Forbidden. Eerst gecontroleerd of het misschien weer een
scope- of rate-limit-probleem was (nee, beide uitgesloten), toen simpelweg
`sp.track(één_id)` op dezelfde ID geprobeerd: werkte meteen. Blijkbaar is
het batch-endpoint (net als eerder de audio-features- en popularity-
endpoints, zie Les 6 / `RECCOBEATS.md`) verder ingeperkt voor onze
toegangsklasse dan het single-item endpoint. Fix: gewoon 107 losse calls
i.p.v. 3 batch-calls — met onze bestaande throttling/retry kostte dat een
paar seconden extra, geen enkel probleem.
→ Als een API "logisch equivalente" batch- en single-item-endpoints heeft
en er iets vreemd 403't, test dan altijd eerst de single-item-variant voor
je verder zoekt naar scope/auth-fouten — de twee endpoints kunnen
losstaand van elkaar ingeperkt zijn.

**Les 10 — `retries=0` (Les 5) loste het "urenlang stil blokkeren"-probleem
op, maar verborg daarbij de échte `Retry-After` en `reason` van élke 429.**
Bij het bouwen van een self-calibrerend quota-systeem (zie `quota.py`)
bleek `e.headers.get("Retry-After", 1)` steeds op de fallback-waarde `1`
uit te komen, en `e.reason` was nooit `"QUOTA_EXCEEDED"` — zelfs niet
tijdens een bevestigd actieve, urenlange blokkade. Oorzaak: met `retries=0`
onderschept urllib3's retry-adapter een 429 nog steeds (want die zit in de
*default* `status_forcelist`), en omdat er met `total=0` niets meer te
retryen valt, gooit hij een `requests.exceptions.RetryError` i.p.v. een
normale `HTTPError`. Spotipy's foutafhandeling voor die twee gevallen is
niet gelijk: de `HTTPError`-tak leest de JSON-body uit voor de echte
headers/`reason`, maar de `RetryError`-tak doet dat niet en construeert een
kale, generieke `SpotifyException` — dus precies de informatie die we
nodig hadden (om QUOTA_EXCEEDED te herkennen en niet te retryen, zie
hieronder) ging al verloren vóórdat onze eigen code er ooit bij kon.
Fix: `status_forcelist=[999]` meegeven aan `spotipy.Spotify(...)` — een
onmogelijke statuscode, zodat urllib3 nooit meer denkt dat het iets moet
retryen, en élke fout (inclusief 429) gewoon via het normale, correct-
parserende pad loopt. Let op de valkuil: `status_forcelist=[]` werkt niet,
want spotipy doet intern `status_forcelist or self.default_retry_codes` —
een lege lijst is *falsy* in Python, dus dat valt stilzwijgend terug op de
default (die 429 juist wél bevat).
→ Bevestigd na de fix: dit was al die tijd inderdaad een echte
`QUOTA_EXCEEDED` (niet een gewone rate limit) — dat wisten we dus pas
zeker nadat we de verborgen informatie weer zichtbaar hadden gemaakt.
→ Als een library "geen retries" belooft via een simpele parameter, check
dan of de onderliggende foutafhandeling (headers, foutdetails) ook
volledig intact blijft in dat pad — een library kan retries uitzetten en
tegelijk stilzwijgend informatie laten verdwijnen via een ander pad.

**Feature: self-calibrerend Search-quota-budget (`quota.py`).** Spotify
publiceert het quotum voor Development Mode-apps niet (en het kan
wijzigen), dus i.p.v. blind een vast getal aan te houden, ontdekken we het
empirisch: een run die zijn eigen zelfopgelegde limiet opbrengt zónder een
echte 429 te krijgen, verhoogt de limiet met 10 voor de volgende keer
(additive increase — vergelijkbaar met TCP-congestiecontrole). Een run die
wél een echte `QUOTA_EXCEEDED` krijgt, zet de limiet direct terug naar het
aantal calls dat in de afgelopen 24 uur daadwerkelijk lukte (snap-to-
ceiling) en onthoudt tot wanneer we geblokkeerd zijn — een volgende run
checkt dat *voordat* er ook maar één Spotify-call gedaan wordt. Gestart op
300 calls/24u (zie eerdere overweging), en meteen bij het bouwen bevestigd
tegen de echte, actieve blokkade van vandaag (zie Les 10 hierboven).
