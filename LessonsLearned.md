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

*Bugfix achteraf:* de eerste versie verhoogde de limiet bij élke run zonder
blokkade, ook als die run maar een paar tientallen calls deed en de limiet
nooit ook maar benaderde — een paar kleine, onschuldige sessies zouden de
limiet dan zonder enig bewijs voor extra ruimte laten oplopen. Fix: alleen
verhogen als de run zijn eigen limiet ook daadwerkelijk heeft opgezocht
(`QuotaExhausted` is gegooid) *en* daarbij geen echte blokkade kreeg — dat
is de enige situatie met echt bewijs dat er ruimte over is.
→ Bij "verhoog bij succes"-logica: check dat "succes" ook echt betekent dat
je de grens hebt opgezocht, niet gewoon "er ging niets mis" — die twee
lijken op elkaar maar zijn niet hetzelfde bewijs.

**Les 11 — ReccoBeats als fallback-bron, en een matching-eigenaardigheid
die er niet bij Spotify is.** Toen Spotify's eigen Search geblokkeerd was
(zie Les 10), bleek `api.reccobeats.com/v1/track/search` een bruikbare,
gratis, ongeauthenticeerde tweede bron — geeft altijd een Spotify-URI
terug zodra hij iets vindt. Twee dingen die anders werken dan bij Spotify:
1. **`searchText` is (vrij) letterlijk, niet fuzzy.** `"{artist} {title}"`
   samen ("Blind Guardian Bright Eyes") gaf vaak **0** resultaten, ook voor
   nummers die wél in hun catalogus zitten — die exacte frase komt
   nergens letterlijk voor. Een titel-only query ("Bright Eyes") vond
   'm wél, gewoon op positie 9 van 200 resultaten. Fix: zoek op titel
   alleen (met een grotere pagina, `size=50`, voor betere dekking) en laat
   onze eigen `best_candidate()`/artiest-matching de juiste artiest eruit
   filteren — precies dezelfde aanpak als bij Spotify's eigen, soms-
   onbetrouwbare ranking (Les 7).
2. **Circulaire import bij hergebruik van de matching-logica.** `reccobeats.py`
   heeft dezelfde `best_candidate()`-logica nodig als `sync_playlist.py`,
   maar `sync_playlist.py` moet op zijn beurt `reccobeats` kunnen
   aanroepen als fallback — een directe import over en weer loopt vast.
   Opgelost door de tekst-matchlogica (die toch geen Spotify-specifieke
   code bevat) te verhuizen naar een eigen `matching.py`, waar beide
   modules onafhankelijk van importeren.

Nieuwe kolommen op `tracks`: `source` ('spotify' = direct bevestigd,
'reccobeats' = alleen via de fallback gevonden) en `reccobeats_id`.
`get_cached_match()` behandelt `source='reccobeats'`-rijen als *niet*
definitief gecached, dus een latere run probeert Spotify vanzelf opnieuw en
upgrade't `source` bij succes — zonder duplicaat (getest: track-aantal
blijft gelijk, `reccobeats_id` blijft bewaard voor het betrouwbaarheids-
inzicht). Live getest tegen de actieve blokkade van vandaag: 7/8 losse
testnummers correct gematcht via ReccoBeats, en één upgrade-scenario
(Spotify bevestigt exact dezelfde track die ReccoBeats al gaf) geverifieerd.

*Vervolg (2026-09-19, proactief backlog matchen):* ReccoBeats heeft (voor
zover geobserveerd) geen eigen dagquota, dus naast de fallback-rol kan het
ook **proactief** de matching-achterstand wegwerken zonder ooit het
Spotify-budget aan te spreken — zie `match_reccobeats_backlog.py`. Bij een
run van 500 nog nooit geprobeerde nummers crashte de hele batch op de
eerste onverwachte fout: `searchText=Pa` (een echt bestaand nummer, Doe
Maar - "Pa") gaf een kale `400 Bad Request` — ReccoBeats vereist
`searchText` van minimaal 3 tekens (`"size must be between 3 and 1000"`).
Fix: titels korter dan 3 tekens meteen als "geen match" overslaan (geen
call nodig), plus algemene robuustheid in `_get_with_retry()` — een
onverwachte non-429 HTTP-fout op één nummer geeft nu `None` terug i.p.v. de
hele batch te laten crashen. Daarna 353/500 (70,6%) succesvol gematcht.
→ Bij een batch-script over honderden losse externe API-calls: één
onvoorziene edge case (hier: een simpele lengte-eis) mag nooit de hele
batch laten crashen — vang fouten per item af, niet alleen per verwachte
foutcode (429).

**Les 12 — Een primary key veranderen in SQLite: tabel opnieuw opbouwen,
en de ouder-tabel nooit hernoemen.** Voor issue #2 (database los van
Spotify) moesten vier tabellen van `spotify_uri` naar `track_id` als
sleutel. SQLite kan geen primary key aanpassen met `ALTER TABLE`, dus
elke tabel wordt opnieuw opgebouwd: `*_new` maken, kopiëren, oude droppen,
hernoemen. Valkuil (bewust vermeden, niet zelf tegenaan gelopen): `tracks`
hernoemen naar `tracks_old` zou de foreign keys van `track_artists` en
`track_match` stilletjes naar de *hernoemde* tabel laten wijzen. Daarom
wordt de nieuwe tabel hernoemd naar `tracks`, nooit andersom, en behouden
de rijen hun oude `id`, zodat alle FK's kloppen. Verder:
- de migratie draait in `db.connect()` **vóór** `SCHEMA`, anders slaat
  `CREATE TABLE IF NOT EXISTS` de oude tabellen over en blijven ze in de
  verkeerde vorm staan;
- alles in één transactie, met een controle van de rijaantallen vóór en
  na; klopt het niet, dan rollback (getest met een kunstmatige wees-rij in
  `playlist_tracks`: migratie faalt, oude database onaangeroerd);
- de oude kolom `tracks.source` deed twee dingen tegelijk ("welke dienst"
  én "hoe zeker is de match") en is daarom gesplitst in `service` en
  `verified`. Zonder die splitsing had een tweede muziekdienst niet
  gepast.
→ Bij een schemawijziging die een sleutel raakt: eerst een backup, dan de
migratie testen op een *kopie* (twee keer achter elkaar draaien, zie Les
8), en vergelijk de inhoud via de nieuwe API met een snapshot van vóór de
migratie — rijaantallen alleen zeggen niet dat de inhoud klopt.

**Les 13 — Een "geen match" mag alleen gecachet worden door de bron die het
echt heeft geprobeerd.** Tijdens de eerste echte Spotify-run na de ban
(`sync_playlist.py pingclass`) raakte Spotify's eigen budget op na 300
calls en nam ReccoBeats het over voor de rest. Bij een ReccoBeats-miss
schreven `sync_playlist.py` en `build_playlist.py` toch een definitief
"geen match" weg (`track_id = NULL`), terwijl Spotify dat nummer nooit had
gezien: 361 nummers zijn zo onterecht afgeschreven en zouden nooit meer bij
Spotify zijn beland. `match_reccobeats_backlog.py` deed dit al goed (een
ReccoBeats-miss blijft "ongeprobeerd"), maar de twee andere scripts niet.
Fix: alleen cachen als de bron in kwestie daadwerkelijk is gevraagd
(`source` is gezet); anders het nummer overslaan. De 361 onterechte rijen
zijn herkend aan hun tijdstip (na de laatste `search_calls`-regel) en uit
`track_match` verwijderd; de 86 echte Spotify-missers bleven staan.
Gerelateerd: de limiet stond na de blokkade op 0 omdat `record_block()` de
limiet zet op het aantal gelogde calls van de laatste 24u, en de calls
die de blokkade veroorzaakten stonden nog niet in het logboek. Een aantal
van 0 zegt niets over het echte plafond en wordt nu genegeerd.
→ Een negatief resultaat ("bestaat niet") is een bewering over één bron,
en hoort alleen geregistreerd te worden voor die bron. Een fallback die
níéts vindt, mag nooit "niets" als definitief antwoord van de primaire bron
laten doorgaan. En: een teller die uit een logboek wordt afgeleid, is
alleen zo goed als het logboek compleet is.

*Vervolg op Les 13 (2026-09-20, `resolve.py`):* dezelfde beslislogica
("welke bron vragen we, en wat mag er gecachet worden") stond dubbel in
`sync_playlist.py` en `build_playlist.py`, en de Les 13-bug moest daardoor
op twee plekken worden gerepareerd. Nu staat het op één plek
(`resolve.py`, klasse `Resolver`) en gebruiken beide scripts die. Tegelijk
opgelost (issue #3): een ReccoBeats-miss wordt onthouden als
`track_match(service='reccobeats')` — een claim over ReccoBeats alleen,
niets over Spotify — zodat dezelfde missers niet elke run opnieuw ~2
seconden kosten; en een eerder gevonden ReccoBeats-kandidaat wordt bij een
volgende run hergebruikt in plaats van opnieuw opgezocht. Bijkomend: scripts
die een half uur draaien moeten onderweg iets laten zien — beide scripts
printen nu elke 100 nummers een voortgangsregel (aantal, lookups per bron,
verstreken tijd), omdat een stille run van 15+ minuten er "bevroren" uitziet.
→ Logica die bepaalt wat je als waar opslaat, hoort op één plek te staan;
twee kopieën repareer je uiteindelijk één keer te weinig.

**Les 14 — Twee bronnen voor dezelfde afspeelgeschiedenis dedupliceren is
geen kwestie van dezelfde sleutel.** Relisten.nl als tweede bron naast
OnlineRadioBox: de PRIMARY KEY van `plays` (zender, artiest, titel,
tijdstip) leek genoeg, ook met tijdstippen op de minuut (Les 2). Gemeten op
één Radio 2-dag (272 plays in beide bronnen): slechts 12% viel op dezelfde
minuut; de rest week 1–3 minuten af (relisten loopt voor). En de tekst
verschilt: accenten (Tiësto/Tiesto), `&`/`,`/`Ft.`, "Cult, The"/"The Cult",
onzichtbare spaties, en soms een ander naamgeving ("Adele"/"Adkins, A").
Stap voor stap verbeterd, telkens gemeten op een kopie van de database:
tijdvenster van 5 min → 82 dubbelen op ~1.500 plays; tekst genormaliseerd →
30; vergelijken op de genormaliseerde *titel* alleen (artiest genegeerd,
haakjes-versies gestript) → 5 (0,4%). Artiest weglaten klinkt riskant maar
twee verschillende nummers met dezelfde titel binnen 5 minuten op één zender
komen praktisch niet voor.
→ Dedupliceren over bronnen heen: meet eerst hoe de bronnen echt van elkaar
verschillen (tijd én tekst) in plaats van te raden, en kies een sleutel op
wat wél betrouwbaar overeenkomt.
Bijkomend: de dagpagina van relisten bevat ook zijbalk-"nieuwe tracks" met
een andere datum — filter op datum. En: een externe koppeling (relisten →
Spotify-id) is een *kandidaat*, geen waarheid; ~7% fout in een steekproef.

**Les 15 — Gegarbelde brontekst: meet per aanpak wat het oplevert, en
hergebruik zoekresultaten die je al hebt.** Ticket #4 (Zeilsteen matcht maar
50%): de missers bleken vooral tekstproblemen. Getest op 200 willekeurige
échte ReccoBeats-missers (van 903 waar Spotify nog niets over gezegd had),
elke aanpak los gemeten: **23 van 200 (11,5%) teruggevonden** — artiest en
titel omdraaien 16 (8%), titel opschonen (versietags "(albumversie)",
"- Radio Edit", "They re" → "They're") 6 (3%), een uit elkaar gevallen
artiest weer samenvoegen ("Hard" + "Fi - Living…" → "Hard-Fi") 1. Niet
opgelost: generieke titels ("Peace", "Girl": ReccoBeats' 200-resultaten-limiet)
en slug-achtige tekst ("Gallagher s-High-Flying-Birds- -The-Dying-Of-…") —
voor die eerste moet Spotify het doen.
- Eerst de opschoonregel te ruim gemaakt: " - iets" achter een titel weghalen
  sloeg ook "Fi - Living For The Weekend" plat tot "Fi". Nu alleen echte
  versietags (edit/remaster/version/mix/live/…/jaartal).
- Voor Spotify kosten de andere lezingen niets extra: `best_candidate` draait
  lokaal, dus alle lezingen worden op dezelfde zoekresultaten geprobeerd. Alleen
  als dat niets oplevert volgt één extra call (de gewone zoekopdracht, die ook
  een omgedraaid paar aankan).
- ReccoBeats geeft bij snel achter elkaar zoeken korte 429's ("rate limited,
  waiting 1–4s"): geen dagquota (zover bekend), wel een burst-limiet die de
  bestaande retry opvangt.
- Een miss is bij een verbeterd algoritme niet meer definitief: missers van
  vóór `reccobeats.ALGORITHM_DATE` kunnen opnieuw (`--retry-misses`).
→ Bij een matching die niets vindt: meet eerst welke tekstafwijkingen echt
voorkomen (steekproef van echte missers), bouw dan alleen wat meetbaar
oplevert, en laat een miss niet permanent zijn als het algoritme verandert.
