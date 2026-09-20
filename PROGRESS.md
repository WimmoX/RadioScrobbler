# RadioScrobbler — voortgang

## Doel
Spotify-playlists automatisch vullen met nummers die recent gedraaid zijn op
bepaalde internetradiozenders, zodat je altijd verse muziek hebt zonder
reclame.

**Scope:**
- **MVP**: playlist bijgewerkt met nummers van de afgelopen periode voor één
  zender (Pinguin Classics).
- **Level 1**: nummers die niet meer gedraaid worden, vallen er ook weer af.
- **Level 2** *(nog niet gebouwd)*: aparte playlists per dagdeel (bv.
  weekdag-dag/avond, vrijdag-middag/avond, zaterdag-ochtend/avond,
  zondag-ochtend/middag/avond).
- **Einddoel** *(nog niet gebouwd)*: Docker-container met minimale UI om
  zender + muziekdienst te kiezen.

## Architectuur

### Databron: OnlineRadioBox, niet Pinguin Radio zelf
Pinguin Radio's eigen site (pinguinradio.com) is een Angular SPA zonder
server-rendered data — de tracklist wordt clientside opgehaald, waarschijnlijk
via een Supabase-backend, maar de query zit in een lazy-loaded JS-chunk die we
niet zonder browser konden vinden. In plaats daarvan gebruiken we
**OnlineRadioBox** (`onlineradiobox.com/nl/{slug}/playlist/{day}`), dat voor
duizenden zenders tijd + artiest + titel bijhoudt. Vereist een
`X-Requested-With: XMLHttpRequest`-header voor de per-dag AJAX-variant.

Bekende zenders (`stations.py`):
| Zender | Station-ID |
|---|---|
| Pinguin Classics | `pingclass` |
| KINK Classics | `kinkclassics` |
| KINK Distortion | `kinkdistortion` |
| Zeilsteen Radio | `zeilsteen` |
| SLAM! Non Stop | `slamnonst` |
| NPO Radio 2 | `radio2` |

OnlineRadioBox groepeert zenders per land in de URL (`/nl/...`, `/uk/...`,
etc.). Een station-ID is dus óf een kale slug (dan gaan we uit van `nl`), óf
`locale/slug` voor zenders onder een ander land — zie `_split_station()` in
`scraper.py`.

**Beperking**: OnlineRadioBox bewaart zelf maar **7 dagen** geschiedenis.
Voor een langer venster (bv. 2-3 weken) moet het scrape-script regelmatig
(handmatig of via cron) draaien — de lokale database stapelt dat vanzelf op.

### Lokale SQLite-database (`db.py`)
Losse database i.p.v. rechtstreeks tegen Spotify praten, om drie redenen:
1. Langere geschiedenis opbouwen dan de 7 dagen die OnlineRadioBox geeft.
2. Spotify Search-resultaten cachen, zodat we nooit twee keer naar hetzelfde
   nummer zoeken (zie "Les 1" hieronder).
3. Lokaal bijhouden wat er (naar ons weten) al in een Spotify-playlist staat,
   zodat we alleen het verschil (toevoegen/verwijderen) naar Spotify hoeven
   te sturen in plaats van steeds de hele boel te herbouwen.

Tabellen:
- `plays` — station_slug, artist, title, played_at (ruwe scrape-data, dedupliceert vanzelf op basis van PRIMARY KEY). Plus drie *generated columns*, puur afgeleid van `played_at` (nooit apart opgeslagen, dus nooit uit sync te raken): `daynr` (ma=1..zo=7), `hr` (uur 0-23), `daypart` (0=nacht/1=ochtend/2=middag/3=avond). Handig voor selecties als "zondagochtend" of "weekend-energy" (Level 2, dagdeel-playlists) zonder dat er ooit een backfill-script voor nodig was — zie Les 8.
- `artists` (id, naam) / `tracks` (id, titel, `isrc`) / `track_artists` (n-op-n koppeling — een nummer kan meerdere artiesten hebben) — genormaliseerde matching-laag, sinds 2026-09-19 (was: platte `spotify_matches`-tabel). Bevat de écht van Spotify afkomstige, gestructureerde artiestenlijst (niet onze eigen zoek-tekst), dus geen fragiele string-splitting meer nodig om erachter te komen welke artiesten bij een nummer horen. Een `track` is sinds 2026-09-20 muziekdienst-onafhankelijk (issue #2): het heeft een eigen id, en de verwijzing naar Spotify (of een andere dienst) staat in `track_services`. `isrc` is de internationale opnamecode — de enige sleutel die alle diensten delen; wordt nu alleen gevuld als ReccoBeats 'm meegeeft (0 van 1.227 tracks per 2026-09-20, want de bestaande matches zijn van vóór die koppeling).
- `track_services` (track_id, `service`, `external_id`, `verified`) — één rij per (track, dienst): `service='spotify'` met de volledige `spotify:track:...`-URI (dat is wat Spotify's API verwacht), `service='reccobeats'` met het ReccoBeats-id (geen luisterdienst, maar wel een plek met een eigen id voor de opname). `verified=1` = de dienst zélf heeft de match bevestigd; `verified=0` = alleen een kandidaat-id via de ReccoBeats-fallback (zie Les 11) — telt als niet-definitief-gecached totdat een latere run Spotify erbij haalt. Een tweede dienst toevoegen = rijen met een andere `service`, geen schemawijziging.
- `track_match` (artiest-tekst, titel-tekst, `service`) → track_id (of `NULL` = bewust "geen match gevonden bij die dienst", ook gecached) — de brug tussen `plays`' platte tekst en een genormaliseerd `track`. `plays` zelf blijft platte tekst (zie hierboven) en wordt dus nooit verplicht een match te hebben voordat een scrape kan landen. `service` zit in de sleutel zodat "Spotify vond niets" een andere dienst later niet blokkeert.
- `playlists` (station_slug, `service`) → playlist_id — voorkomt dat we elke run opnieuw playlists moeten opzoeken.
- `playlist_tracks` — wat we denken dat er (per zender) in de playlist staat; op `track_id`.
- `blocklist` — nummers die je zelf uit een playlist hebt verwijderd; worden nooit automatisch opnieuw toegevoegd; op `track_id`.
- `audio_features` — cache van ReccoBeats-kenmerken per `track_id` (energy, valence, danceability, tempo, etc.).

De functies in `db.py` nemen en geven nog steeds de id's van een dienst (Spotify-URI's, `service="spotify"` als default) en vertalen intern naar `track_id` — zo blijven `sync_playlist.py`, `build_playlist.py` enz. vrijwel ongewijzigd. Een onbekend id geeft een `KeyError`. Bewust nog **niet** opgelost: dezelfde opname op twee diensten aan één `track` koppelen (dat gaat via ISRC, en hoort bij het bouwen van de eerste tweede integratie — dit ticket bereidt alleen voor).

### Scripts
- **`quota.py`** (geen los script, een module) — `SearchBudget`: zelf-
  calibrerend budget voor Spotify Search-calls, zie Les 10 in
  `LessonsLearned.md`. Gebruikt door `sync_playlist.py`/`build_playlist.py`
  vóór elke nieuwe matching-poging.
- **`matching.py`** (module) — de tekst-matchlogica (`best_candidate()`,
  titel-/artiestvergelijking) losgetrokken uit `sync_playlist.py` zodat
  `reccobeats.py` 'm ook kan gebruiken zonder circulaire import (zie Les 11).
- **`reccobeats.py`** (module) — `search_track()`: gratis, ongeauthenticeerd
  fallback-zoekpad via ReccoBeats voor als Spotify's Search geblokkeerd is.
  Wordt automatisch ingezet door `sync_playlist.py`/`build_playlist.py`
  zodra `quota.QuotaBlocked`/`QuotaExhausted` optreedt — de rest van die run
  gaat dan verder op ReccoBeats i.p.v. te stoppen.
- **`match_reccobeats_backlog.py [--limit N]`** — proactief (dus niet als
  fallback, maar los ingepland) de matching-achterstand wegwerken via
  ReccoBeats, zonder het Spotify-budget aan te spreken (ReccoBeats heeft
  vooralsnog geen eigen quota getoond). Misses worden bewust niet als
  "definitief geen match" opgeslagen — Spotify mag het later nog proberen.
- **`scrape.py [station]`** — haalt de laatste 7 dagen op van OnlineRadioBox en
  slaat ze op in `plays`. Geen Spotify-calls, dus altijd veilig om te draaien.
  Draai dit regelmatig (dagelijks) om historie op te bouwen.
- **`sync_playlist.py [station]`** — leest de laatste 14 dagen uit de
  database, matcht alleen *nieuwe* artiest/titel-combinaties via Spotify
  Search (rest komt uit cache), berekent lokaal het verschil met wat er al in
  de playlist staat (`to_add` / `to_remove`), en stuurt alléén dat verschil
  naar Spotify. Nummers op de blocklist worden nooit toegevoegd; geliked
  nummers (via Spotify's "Liked Songs") worden nooit verwijderd.
- **`resync_playlist.py [station]`** — haalt de écht huidige inhoud van de
  Spotify-playlist op (voor het geval je zelf iets hebt toegevoegd/verwijderd
  in de Spotify-app), zet de lokale `playlist_tracks`-cache gelijk aan de
  realiteit, en voegt handmatig verwijderde nummers toe aan de blocklist.
  Af en toe handmatig draaien, na eigen wijzigingen in Spotify.
- **`stations.py`** — mapping van zender-slug naar playlistnaam, gedeeld door
  de scripts.
- **`add_station.py <zoekterm> <playlistnaam>`** — doorzoekt OnlineRadioBox,
  laat je een kandidaat kiezen, en voegt die toe aan `stations.py`.
- **`remove_station.py <station-id>`** — het spiegelbeeld: haalt de zender
  uit `stations.py`, wist de `plays`-historie, en verwijdert uit élke
  gevolgde playlist (`playlist_tracks`) de nummers die *uitsluitend* van die
  zender kwamen (nummers die ook op een andere zender draaien blijven
  staan). Dit was tot nu toe telkens een handmatig ad-hoc scriptje (zie de
  KINK Radio- en BBC Radio 6-verwijderingen); nu een herbruikbaar commando.
  Getest met een volledige round-trip (zender toevoegen → scrapen → nummer
  in de live playlist zetten → weer verwijderen) — `stations.py` kwam er
  exact hetzelfde uit, en het testnummer werd correct uit de live playlist
  gehaald.
- **`build_playlist.py <playlistnaam> [--daynr N ...] [--daypart N ...] [--top N] [--exclude-station ID ...]`**
  — bouwt/update een playlist met de top-N nummers binnen een dag/dagdeel-
  venster (`daynr`/`daypart`, zie hierboven), bv. "vrijdagavond" of, door
  meerdere `--daynr`/`--daypart` mee te geven, een "weekend energy"-combi.
  Hergebruikt dezelfde matching/retry/liked-bescherming als
  `sync_playlist.py`. Print bewust alleen een korte samenvatting (aantal
  kandidaten, aantal matches, playlist-link) — de volledige per-nummer-log
  gaat naar een bestand onder `logs/` (genegeerd door git), zodat het
  draaien van dit script niet afhangt van hoeveel data erdoorheen gaat.
  Ontstaan uit een expliciete wens om dit soort playlist-opbouw als
  losstaand, herhaalbaar commando te kunnen draaien i.p.v. ad-hoc via losse
  Python-snippets (wat tot dan toe de gangbare aanpak was voor de
  handmatige top-30-selecties).
- **`fetch_audio_features.py`** — haalt voor alle al-gematchte Spotify-tracks
  (uit `tracks`) de ReccoBeats-kenmerken op (energy, valence,
  danceability, tempo, ...) en cachet ze in `audio_features`. Geen API-key of
  Spotify-login nodig, kan onafhankelijk van `sync_playlist.py` draaien
  zodra er matches zijn. Zie `RECCOBEATS.md`.

### Config
`.env` (niet in git, zie `.env.example`): Spotify client ID/secret/redirect
URI, standaard zender-slug/playlist-naam, en `DB_PATH` (pad naar het
SQLite-bestand — kan wijzen naar een gemounte Synology-share zodat de
geschiedenis persistent is, nu nog lokaal op de Mac).

## Lessons learned
Zie `LessonsLearned.md` voor opgeloste problemen (rate limits, dedup-bugs,
scraping-aanpak) — apart bestand zodat we niet steeds tegen hetzelfde
aanlopen. Voor Spotify specifiek: `SpotifyAPI.md` is het opgeschoonde
naslagwerk (endpoints, quota-gedrag, scopes, spotipy-valkuilen,
foutmeldingen) — begin daar als je met de Spotify-integratie werkt.

## Status (2026-09-08)
- **KINK Radio (`kink`) is uit het systeem gehaald** op verzoek (muzieksmaak
  paste niet) — verwijderd uit `stations.py`, alle `plays`-data voor die
  zender gewist, en de 14 nummers die *uitsluitend* via KINK Radio in
  "RadioScrobbled" terechtkwamen zijn er ook weer uitgehaald (nummers die
  ook op andere zenders draaiden zijn blijven staan). KINK Classics en KINK
  Distortion blijven gewoon meedoen — dit was specifiek de brede "KINK
  Radio"-zender, niet het hele KINK-merk.
- **BBC Radio 6 Music is ook uit het systeem gehaald** op verzoek (muziek
  beviel niet) — zelfde behandeling: uit `stations.py`, alle `plays`-data
  gewist (2064 rows), en de 24 nummers die *uitsluitend* via deze zender in
  "RadioScrobbled" stonden eruit gehaald (6 bleven staan, want die draaiden
  ook op Pinguin Classics/KINK Classics). Dit was tegelijk de enige zender
  met een niet-`nl`-locale (`uk/bbcradio6`); de multi-locale support in
  `scraper.py` blijft gewoon bestaan voor een eventuele toekomstige zender.
- Scrapen (`scrape.py`) getest en werkt voor de 3 resterende zenders,
  dedup bevestigd over meerdere runs.
- `sync_playlist.py` **werkt nu end-to-end, bevestigd in productie**: eerste
  echte playlist ("RadioScrobbled", een handmatige top-30-selectie over alle
  zenders behalve KINK Distortion) succesvol aangemaakt en gevuld op Spotify.
  Onderweg nog twee bugs gevonden en gefixt die alleen in de praktijk (niet
  in code review) aan het licht kwamen: spotipy's verborgen interne retry
  (Les 5) en het door Spotify geblokkeerde `user_playlist_create()`-endpoint
  (Les 6). `resync_playlist.py` is met dezelfde onderliggende functies
  gebouwd maar nog niet apart end-to-end getest.
- Matching-logica in `find_track_uri()`/`_best_candidate()` flink verbeterd
  na een echte misser (Royal Blood) en enkele valse afwijzingen (accenten,
  Spotify-titeltoevoegingen zoals "(feat. X)" of "- Single Version") — zie
  Les 7 en het vervolg daarop in `LessonsLearned.md`. "RadioScrobbled" staat
  nu op 30 correct gematchte nummers.
- Nog niet gebouwd: Level 2 (dagdelen), Docker/UI, en de "Verbannen Nummers"-feature
  (zie hieronder).
- `fetch_audio_features.py` gebouwd en getest — werkt en cachet correct. Nu
  ook echt bruikbaar: `spotify_matches` bevat sinds vandaag voor het eerst
  échte matches.
- **2026-09-18**: `add_station.py`/`remove_station.py` toegevoegd (zender
  toevoegen/verwijderen is nu een commando i.p.v. handwerk). Daarnaast
  `plays` uitgebreid met `daynr`/`hr`/`daypart` (generated columns,
  afgeleid van `played_at`) als basis voor Level 2 (dagdeel-playlists) —
  zie hierboven en Les 8. Migratie getest tegen de echte database, incl.
  een bug gevonden en gefixt vóórdat 'ie live kon gaan (zie Les 8).
  `build_playlist.py` gebouwd om daar meteen iets mee te doen: eerste run
  ("RadioScrobbler - Vrijdagavond", daynr=5/daypart=3, top 30) leverde
  29/30 gematchte nummers op, live op Spotify.
- **2026-09-19**: matching-laag genormaliseerd — `spotify_matches` vervangen
  door `artists`/`tracks`/`track_artists`/`track_match` (zie hierboven).
  `plays` blijft bewust ongewijzigd (platte tekst, geen FK naar een track).
  Bestaande 107 matches gemigreerd via `migrate_normalize_matches.py`, met
  écht van Spotify opgehaalde artiestenlijsten (niet onze eigen zoek-tekst)
  — daarbij Les 9 tegengekomen (batch-endpoint `/v1/tracks` geeft 403,
  single-item `/v1/tracks/{id}` werkt gewoon). Geverifieerd: 0 verschillen
  tussen oude en nieuwe data (109/109 rijen), en alle scripts
  (`sync_playlist.py`, `build_playlist.py`, `fetch_audio_features.py`,
  `remove_station.py`) opnieuw getest tegen de nieuwe tabellen — werken
  allemaal. Oude tabel daarna verwijderd.
- **2026-09-20**: database losgekoppeld van Spotify (issue #2) — nieuwe
  tabel `track_services`; `tracks` heeft alleen nog `id`/`title`/`isrc`;
  `playlist_tracks`, `blocklist` en `audio_features` staan nu op `track_id`;
  `playlists` en `track_match` hebben een `service`-kolom. De migratie
  draait automatisch (en eenmalig) in `db.connect()` in één transactie en
  draait zichzelf terug als een rijaantal niet klopt. Geverifieerd op een
  kopie én op de echte database: 1.227 tracks (690 `verified`), 59
  playlist-regels, 325 audio_features, 1.286 track_match-rijen (56
  "geen match") — allemaal identiek aan vóór de migratie; `connect()` twee
  keer achter elkaar werkt; `PRAGMA foreign_key_check`/`integrity_check`
  schoon. Zie Les 12. Ook: `reccobeats.search_track()` geeft nu de `isrc`
  mee, en `resync_playlist.py` registreert handmatig toegevoegde nummers als
  track (met titel/artiesten uit Spotify) omdat de tabellen nu een track
  vereisen. Onderweg ook een bestaande bug in `resync_playlist.py` gevonden
  en gefixt: die las nog `item["track"]` i.p.v. `item["item"]` (zie
  `SpotifyAPI.md`), zou dus een lege playlist zien en daardoor álle bekende
  nummers op de blocklist zetten. Read-only getest tegen de echte playlist:
  29 op Spotify, 29 lokaal bekend, geen verschillen.
- **2026-09-20 (Spotify-ban voorbij, eerste echte batches)**: quota-limiet
  teruggezet van 0 naar 300 (zie Les 13) en de stap opgehoogd van +10 naar
  **+25** (anders duurt het te lang om het plafond te vinden). Eerste run
  (`sync_playlist.py pingclass`): 300 Spotify-calls zonder één 429 — het
  echte plafond ligt dus boven 300 — daarna nam ReccoBeats het over; limiet
  werd 310. Tweede batch (`sync_playlist.py kinkclassics`, 09:21): 10 vrije
  Spotify-calls, limiet 310 → **335**; 546/676 tracks in de playlist.
  Gemeten doorlooptijd: Spotify ~0,6 s per call (300 calls ≈ 3 min);
  ReccoBeats ~1,6–2,2 s per lookup (1.464 lookups ≈ 39 min; 455 lookups ≈
  17 min). De trage kant is dus ReccoBeats, niet Spotify — en beide
  scripts printen pas iets *na* afloop, dus een run van 15+ minuten ziet er
  bevroren uit. Bug gevonden en gefixt: een ReccoBeats-miss werd door
  `sync_playlist.py`/`build_playlist.py` als definitieve "geen match"
  gecachet terwijl Spotify het nummer nooit had gezien (361 rijen, zie
  Les 13). Stand daarna: 957 Spotify-bevestigd, 1.692 nog als kandidaat
  (ReccoBeats), 4.983 van de 7.708 unieke nummers nog nooit geprobeerd.
- **2026-09-20 (avond)**: `resolve.py` (klasse `Resolver`) gebouwd — de
  gedeelde beslislogica van `sync_playlist.py`/`build_playlist.py`: cache →
  Spotify (zolang het budget strekt) → ReccoBeats. ReccoBeats-missers worden
  onthouden (`track_match`, `service='reccobeats'`, issue #3), een eerder
  gevonden ReccoBeats-kandidaat wordt hergebruikt, en er komt elke 100
  nummers een voortgangsregel. `match_reccobeats_backlog.py` slaat zijn
  missers nu ook op. Eerste run met de nieuwe code: NPO Radio 2 —
  1.465 unieke nummers, 1.180 nieuwe lookups (allemaal ReccoBeats, Spotify-
  budget was op) in 37m46s (~1,9 s per lookup), 1.151 nummers in de
  playlist. Stand daarna: 978 Spotify-bevestigd, 2.539 ReccoBeats-
  kandidaten, 306 ReccoBeats-missers onthouden, 3.777 van de 7.708 unieke
  nummers nog nooit geprobeerd. Spotify-limiet nu 360/24u.
- **2026-09-19 (later die dag)**: een volledige `sync_playlist.py pingclass`
  (2482 unieke nummers, waarvan ~2100 nog niet gematcht) liep tegen een
  échte Spotify-quota-blokkade aan (~22 uur). Daaruit voortgekomen: een
  zelf-calibrerend budgetsysteem (`quota.py`) i.p.v. een vast aantal — zie
  Les 10. Onderweg ontdekt dat onze eigen `retries=0`-fix (Les 5) de echte
  `Retry-After`/`reason` van élke 429 verborg via een ander spotipy-
  foutpad; gefixt met `status_forcelist=[999]`. Live geverifieerd tegen de
  actieve blokkade van vandaag: `QuotaBlocked` wordt nu binnen 0,08s
  herkend (i.p.v. 5 nutteloze retries), met de échte `reason:
  "QUOTA_EXCEEDED"` en `Retry-After`. Budget gestart op 300 calls/24u.
- **2026-09-19 (nog later)**: `quota.SearchBudget` bugfix (verhoogde limiet
  ten onrechte bij elke kleine, blokkadevrije run — nu alleen als de limiet
  ook echt is opgezocht). Daarna: ReccoBeats als fallback-zoekpad gebouwd
  (`reccobeats.py`) voor als Spotify geblokkeerd is, matching-logica
  losgetrokken naar `matching.py` om de circulaire import te vermijden, en
  `tracks` uitgebreid met `source`/`reccobeats_id`. Live getest tegen de
  nog actieve blokkade van vandaag: 7/8 nummers correct via ReccoBeats
  gematcht, en het "upgrade bij Spotify-bevestiging"-pad geverifieerd
  (geen duplicaat, `source` correct bijgewerkt). Zie Les 11.
- **2026-09-19 (avond)**: 4 nieuwe zenders toegevoegd (Zeilsteen Radio,
  SLAM! Non Stop, NPO Radio 2; NPO 3FM onderzocht maar niet toegevoegd, zie
  hieronder). Vervolgens de matching-achterstand aangepakt: van de 7.708
  unieke nummers over alle zenders was tot dan toe nog maar ~10% ooit
  geprobeerd te matchen. `match_reccobeats_backlog.py` gebouwd om dit
  proactief (los van de fallback-rol) via ReccoBeats weg te werken, zonder
  het Spotify-budget aan te spreken. Onderweg een echte bug gevonden en
  gefixt (een batch van 500 crashte op één kort titeltje, "Pa" — zie Les
  11-vervolg). Na de fix: 353/500 (70,6%) in één run gematcht, waarmee de
  achterstand van ~10% naar ~17% geprobeerd ging.

## Openstaand: "Verbannen Nummers"-playlist (nog niet gebouwd, wacht op akkoord)
Idee: een Spotify-playlist "Verbannen Nummers" als zichtbare, handmatig te beheren
ban-lijst (i.p.v. of naast de lokale `blocklist`-tabel). Open vraag: is dit
één gedeelde/globale lijst voor alle zenders, of per zender? Zodra dat
akkoord is, bouwen: nummers uit main-playlists verwijderd (handmatig of via
resync gedetecteerd) → toegevoegd aan Verbannen Nummers; alles wat in Verbannen Nummers
staat (ook zelf toegevoegd) wordt bij elke sync genegeerd, maar blijft wel
gewoon in de scrape/database staan.

## Openstaand: disambiguatie tussen gelijknamige opnames (user story, nog niet gebouwd)
**Als gebruiker wil ik dat de juiste versie van een nummer gematcht wordt,
ook als er meerdere Spotify-opnames met identieke artiest+titel bestaan
(bv. akoestische vs. studioversie), zodat de playlist het nummer bevat dat
de radio daadwerkelijk speelde.**

Bevestigd probleem (2026-09-18, test met Blind Guardian - "Bright Eyes -
Remastered 2007", twee volledig verschillende opnames met identieke
titel-tekst): onze matcher heeft geen enkel tekstueel signaal om ze te
onderscheiden — hij kiest consistent (deterministisch per exacte query)
maar willekeurig (afhankelijk van Spotify's eigen zoekresultaat-ranking),
niet per se de opname die echt gedraaid is.

Onderzocht: Spotify's eigen `popularity`-veld is sinds de februari 2026 API-
wijzigingen niet meer beschikbaar voor Development Mode-apps (bevestigd,
zie `RECCOBEATS.md`/Les 6-omgeving). **ReccoBeats geeft wél een
`popularity`-score** (los van Spotify's beperking, getest en werkend) —
potentiële tie-breaker voor later, mocht dit in de praktijk echt een keer
misgaan. Bewust niet nu gebouwd: dit was een doelbewust geconstrueerd
testgeval, nooit (nog) opgedoken in de 80+ echte matches tot nu toe, en het
zou een extra ReccoBeats-call in de kern-matchingflow vereisen voor iets
dat mogelijk zelden voorkomt — en zelfs dan geen garantie geeft ("populair"
≠ "wat de radio speelde"). Oppakken zodra het echt een keer fout blijkt te
gaan in de praktijk.

## Openstaand: NPO 3FM heeft geen tracklist-data op OnlineRadioBox
Getest (2026-09-19): OnlineRadioBox heeft voor `npo3fm` op **geen enkele**
van de 7 dagen tracklist-data ("Helaas gaf het radiostation geen playlist
op voor deze dag") — geen tijdelijk gat, de zender levert deze bron
kennelijk niets. Daarom (nog) niet toegevoegd aan `stations.py`.

Wél een bruikbaar spoor gevonden: `npo3fm.nl/gedraaid` heeft zelf een
"laatst gedraaid"-pagina met een ingebed JSON-blok (`__NEXT_DATA__` →
`props.pageProps.trackPlays`) met artiest, titel én exact tijdstip — werkt
betrouwbaar voor de **laatste ~12 nummers van vandaag**. De pagina claimt
17 pagina's (~200 nummers) en een `date`-parameter voor eerdere dagen te
ondersteunen, maar die worden **server-side genegeerd** (zowel de gewone
HTML-route als de lichte `_next/data/{buildId}/gedraaid.json`-route geven
altijd exact dezelfde "vandaag, pagina 1"-data terug, ongeacht `page=`/
`date=`-parameters) — de echte datum/pagina-navigatie gebeurt kennelijk via
een client-side JS-aangeroepen API die niet direct in de HTML/bundle-tekst
te vinden was. Verder uitzoeken zou dieper in de webpack-JS-bundel moeten
graven, of een headless browser vereisen.

Voor nu bewust laten zitten (op verzoek). Als dit ooit wordt opgepakt: een
simpele scraper voor de "laatste 12"-snapshot zou al werken, maar vereist
dan wél veel frequenter scrapen (elke paar uur, niet 1x per week) omdat er
geen enkele terugwerkende historie op te halen is — heel anders dan het
7-dagen-venster van OnlineRadioBox.
